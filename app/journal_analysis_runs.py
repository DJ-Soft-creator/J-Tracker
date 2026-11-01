"""Läufe der Journal-Analyse-Verwaltung.

Ausgelagert aus ``journal_analysis.py`` (1000-Zeilen-Regel). Enthält den
Lebenszyklus eines Tageslaufs — anlegen/öffnen, abschließen, fehlschlagen,
veralten — und den Datei-Export eines einzelnen Laufs.

Die Job-Definition, Prompt-Vorlagen und Benachrichtigungen bleiben in
``journal_analysis.py``; gemeinsame Primitive werden von dort bezogen.
"""

import json
import uuid

from sqlalchemy import text

import journal_analysis as ja


def _as_run(row):
    run = ja._normalize(row)
    run["state"] = ja._json_value(run.get("state"), {})
    return run


def list_runs(owner_id, *, job_id=None, limit=100, engine=None):
    owner = ja._clean_owner(owner_id)
    try:
        limit = max(1, min(int(limit), 1000))
    except (TypeError, ValueError):
        limit = 100
    active_engine, dispose = ja._engine(engine)
    try:
        with active_engine.connect() as connection:
            if job_id:
                rows = connection.execute(text("""
                    SELECT * FROM analysis_runs
                    WHERE owner_id = :owner AND job_id = CAST(:job AS uuid)
                    ORDER BY run_date DESC LIMIT :limit
                """), {"owner": owner, "job": job_id, "limit": limit}).mappings().all()
            else:
                rows = connection.execute(text("""
                    SELECT * FROM analysis_runs
                    WHERE owner_id = :owner
                    ORDER BY run_date DESC LIMIT :limit
                """), {"owner": owner, "limit": limit}).mappings().all()
        return [_as_run(row) for row in rows]
    finally:
        if dispose:
            active_engine.dispose()


def get_run(owner_id, run_id, *, engine=None):
    owner = ja._clean_owner(owner_id)
    active_engine, dispose = ja._engine(engine)
    try:
        with active_engine.connect() as connection:
            row = connection.execute(text("""
                SELECT * FROM analysis_runs WHERE owner_id = :owner AND id = CAST(:id AS uuid)
            """), {"owner": owner, "id": run_id}).mappings().first()
        if row is None:
            raise ja.RunNotFound("Lauf nicht gefunden")
        return _as_run(row)
    finally:
        if dispose:
            active_engine.dispose()


def begin_run(owner_id, job_id, run_date, *, engine=None, now=None, data_root=None):
    """Einen Tageslauf anlegen oder wieder öffnen (idempotent per Job+Datum)."""
    owner = ja._clean_owner(owner_id)
    day = ja._validate_date(run_date, "Laufdatum")
    if day is None:
        raise ja.JournalAnalysisError("Laufdatum fehlt")
    current = ja._now(now)
    active_engine, dispose = ja._engine(engine)
    try:
        with active_engine.begin() as connection:
            job = connection.execute(text("""
                SELECT id, job_version FROM analysis_jobs
                WHERE owner_id = :owner AND id = CAST(:id AS uuid)
            """), {"owner": owner, "id": job_id}).mappings().first()
            if job is None:
                raise ja.JobNotFound("Job nicht gefunden")
            existing = connection.execute(text("""
                SELECT id FROM analysis_runs WHERE job_id = CAST(:id AS uuid) AND run_date = :day
            """), {"id": job_id, "day": day}).mappings().first()
            if existing:
                run_id = str(existing["id"])
                connection.execute(text("""
                    UPDATE analysis_runs SET status = 'running', started_at = :now, completed_at = NULL,
                        error_code = NULL, error_message = '', updated_at = :now
                    WHERE id = CAST(:id AS uuid)
                """), {"now": current, "id": run_id})
            else:
                run_id = str(uuid.uuid4())
                connection.execute(text("""
                    INSERT INTO analysis_runs (
                        id, job_id, owner_id, run_date, status, job_version, summary, state, result,
                        stale, stale_reason, started_at, created_at, updated_at
                    ) VALUES (
                        CAST(:id AS uuid), CAST(:job AS uuid), :owner, :day, 'running', :version,
                        '', '{}'::jsonb, '', false, '', :now, :now, :now
                    )
                """), {"id": run_id, "job": job_id, "owner": owner, "day": day,
                       "version": job["job_version"], "now": current})
            _add_event(connection, run_id, "started", "", current)
            row = connection.execute(text("""
                SELECT * FROM analysis_runs WHERE id = CAST(:id AS uuid)
            """), {"id": run_id}).mappings().first()
        return _as_run(row)
    finally:
        if dispose:
            active_engine.dispose()


def complete_run(owner_id, job_id, run_date, *, result="", summary=None, state=None,
                 source_hash=None, engine=None, now=None, data_root=None,
                 notification_title=None, notify=True):
    """Einen Tageslauf abschließen.

    ``notify=True`` legt wie bisher eine Meldung für genau diesen Tag an. Der
    Worker setzt es auf ``False`` und benachrichtigt stattdessen **einmal am
    Ende des Laufs** über den gesamten Zeitraum (``notify_run``) — sonst gab
    eine Monatsanalyse 30 Einzelmeldungen.
    """
    owner = ja._clean_owner(owner_id)
    day = ja._validate_date(run_date, "Laufdatum")
    if day is None:
        raise ja.JournalAnalysisError("Laufdatum fehlt")
    clean_result = ja._clean_text(result, "Ergebnis", max_length=ja.MAX_RESULT)
    clean_summary = (ja._clean_text(summary, "Zusammenfassung", max_length=ja.MAX_SUMMARY)
                     if summary is not None else None)
    if state is not None and not isinstance(state, dict):
        raise ja.JournalAnalysisError("State muss ein Objekt sein")
    current = ja._now(now)
    active_engine, dispose = ja._engine(engine)
    try:
        with active_engine.begin() as connection:
            job = connection.execute(text("""
                SELECT * FROM analysis_jobs WHERE owner_id = :owner AND id = CAST(:id AS uuid)
            """), {"owner": owner, "id": job_id}).mappings().first()
            if job is None:
                raise ja.JobNotFound("Job nicht gefunden")
            job_dict = ja._as_job(job)
            run = connection.execute(text("""
                SELECT id FROM analysis_runs WHERE job_id = CAST(:id AS uuid) AND run_date = :day
            """), {"id": job_id, "day": day}).mappings().first()
            if run is None:
                run_id = str(uuid.uuid4())
                connection.execute(text("""
                    INSERT INTO analysis_runs (
                        id, job_id, owner_id, run_date, status, job_version, started_at,
                        created_at, updated_at
                    ) VALUES (CAST(:id AS uuid), CAST(:job AS uuid), :owner, :day, 'completed',
                              :version, :now, :now, :now)
                """), {"id": run_id, "job": job_id, "owner": owner, "day": day,
                       "version": job_dict["job_version"], "now": current})
            else:
                run_id = str(run["id"])
            new_summary = clean_summary if clean_summary is not None else job_dict.get("summary", "")
            new_state = state if state is not None else job_dict.get("state", {})
            # Ein erneut bearbeiteter Tag war zuvor vielleicht fehlgeschlagen:
            # Version und Fehlerfelder werden deshalb mit ueberschrieben, sonst
            # bliebe ein alter Fehlertext an einem nun gruenen Lauf kleben.
            connection.execute(text("""
                UPDATE analysis_runs SET status = 'completed', result = :result, summary = :summary,
                    state = CAST(:state AS jsonb), source_hash = :hash, completed_at = :now,
                    started_at = COALESCE(started_at, :now), job_version = :version,
                    error_code = NULL, error_message = '',
                    updated_at = :now, stale = false, stale_reason = ''
                WHERE id = CAST(:id AS uuid)
            """), {"result": clean_result, "summary": new_summary,
                   "state": json.dumps(new_state), "hash": source_hash,
                   "now": current, "id": run_id, "version": job_dict["job_version"]})
            connection.execute(text("""
                UPDATE analysis_jobs SET summary = :summary, state = CAST(:state AS jsonb),
                    last_run_date = :day, updated_at = :now
                WHERE id = CAST(:id AS uuid)
            """), {"summary": new_summary, "state": json.dumps(new_state),
                   "day": day, "now": current, "id": job_id})
            if notify and job_dict["output_notification"]:
                title = notification_title or f"{job_dict['name']} · {day.isoformat()}"
                ja._insert_notification(connection, owner, job_id, run_id, day, title, clean_result,
                                        "analysis", current)
            _add_event(connection, run_id, "completed", "", current)
            run_row = connection.execute(text("""
                SELECT * FROM analysis_runs WHERE id = CAST(:id AS uuid)
            """), {"id": run_id}).mappings().first()
            job_row = connection.execute(text("""
                SELECT * FROM analysis_jobs WHERE id = CAST(:id AS uuid)
            """), {"id": job_id}).mappings().first()
            notifications = connection.execute(text("""
                SELECT * FROM analysis_notifications WHERE owner_id = :owner AND job_id = CAST(:id AS uuid)
                ORDER BY created_at ASC
            """), {"owner": owner, "id": job_id}).mappings().all()
        ja._export_job(owner, data_root, job=job_row, notification_rows=notifications)
        store_run(owner, job_row, run_row, data_root)
        return _as_run(run_row)
    finally:
        if dispose:
            active_engine.dispose()


def fail_run(owner_id, job_id, run_date, *, error_code="", error_message="",
             engine=None, now=None, data_root=None):
    owner = ja._clean_owner(owner_id)
    day = ja._validate_date(run_date, "Laufdatum")
    if day is None:
        raise ja.JournalAnalysisError("Laufdatum fehlt")
    current = ja._now(now)
    active_engine, dispose = ja._engine(engine)
    try:
        with active_engine.begin() as connection:
            job = connection.execute(text("""
                SELECT * FROM analysis_jobs WHERE owner_id = :owner AND id = CAST(:id AS uuid)
            """), {"owner": owner, "id": job_id}).mappings().first()
            if job is None:
                raise ja.JobNotFound("Job nicht gefunden")
            job_dict = ja._as_job(job)
            run = connection.execute(text("""
                SELECT id FROM analysis_runs WHERE job_id = CAST(:id AS uuid) AND run_date = :day
            """), {"id": job_id, "day": day}).mappings().first()
            if run is None:
                run_id = str(uuid.uuid4())
                connection.execute(text("""
                    INSERT INTO analysis_runs (
                        id, job_id, owner_id, run_date, status, job_version, started_at,
                        created_at, updated_at
                    ) VALUES (CAST(:id AS uuid), CAST(:job AS uuid), :owner, :day, 'failed',
                              :version, :now, :now, :now)
                """), {"id": run_id, "job": job_id, "owner": owner, "day": day,
                       "version": job_dict["job_version"], "now": current})
            else:
                run_id = str(run["id"])
            connection.execute(text("""
                UPDATE analysis_runs SET status = 'failed', error_code = :code,
                    error_message = :message, completed_at = :now,
                    started_at = COALESCE(started_at, :now), job_version = :version,
                    updated_at = :now
                WHERE id = CAST(:id AS uuid)
            """), {"code": ja._clean_text(error_code, "Fehlercode", max_length=120),
                   "message": ja._clean_text(error_message, "Fehlertext", max_length=ja.MAX_ERROR),
                   "now": current, "id": run_id, "version": job_dict["job_version"]})
            if job_dict["output_notification"]:
                ja._insert_notification(connection, owner, job_id, run_id, day,
                                        f"{job_dict['name']} · fehlgeschlagen",
                                        ja._clean_text(error_message, "Fehlertext", max_length=ja.MAX_ERROR),
                                        "error", current)
            _add_event(connection, run_id, "failed", "", current)
            job_row = connection.execute(text("""
                SELECT * FROM analysis_jobs WHERE id = CAST(:id AS uuid)
            """), {"id": job_id}).mappings().first()
            run_row = connection.execute(text("""
                SELECT * FROM analysis_runs WHERE id = CAST(:id AS uuid)
            """), {"id": run_id}).mappings().first()
            notifications = connection.execute(text("""
                SELECT * FROM analysis_notifications WHERE owner_id = :owner AND job_id = CAST(:id AS uuid)
                ORDER BY created_at ASC
            """), {"owner": owner, "id": job_id}).mappings().all()
        ja._export_job(owner, data_root, job=job_row, notification_rows=notifications)
        store_run(owner, job_row, run_row, data_root)
        return _as_run(run_row)
    finally:
        if dispose:
            active_engine.dispose()


def mark_run_stale(owner_id, job_id, run_date, *, reason="source_changed",
                   engine=None, now=None):
    """Ein Ergebnis als veraltet markieren — ohne Neulauf (JA-06)."""
    owner = ja._clean_owner(owner_id)
    day = ja._validate_date(run_date, "Laufdatum")
    current = ja._now(now)
    active_engine, dispose = ja._engine(engine)
    try:
        with active_engine.begin() as connection:
            result = connection.execute(text("""
                UPDATE analysis_runs SET stale = true, stale_reason = :reason, updated_at = :now
                WHERE owner_id = :owner AND job_id = CAST(:id AS uuid) AND run_date = :day
            """), {"reason": reason, "now": current, "owner": owner, "id": job_id, "day": day})
            return result.rowcount > 0
    finally:
        if dispose:
            active_engine.dispose()


def _add_event(connection, run_id, event_type, detail, now):
    connection.execute(text("""
        INSERT INTO analysis_run_events (id, run_id, event_type, detail, created_at)
        VALUES (CAST(:id AS uuid), CAST(:run AS uuid), :type, :detail, :now)
    """), {"id": str(uuid.uuid4()), "run": run_id, "type": event_type,
           "detail": detail, "now": now})


def notify_run(owner_id, job, *, day, title, body, run_id=None, engine=None, now=None,
               data_root=None, notify_at=None):
    """Genau eine Meldung für einen ganzen Lauf über mehrere Tage.

    ``day`` und ``run_id`` verweisen auf den letzten bearbeiteten Tag, damit die
    Meldung im Protokoll auffindbar bleibt. Die Rückschau bleibt tagesgenau in
    ``analysis_runs``; die Meldung ist der Hinweis auf das Gesamtergebnis.
    ``notify_at`` schiebt die Sichtbarkeit auf einen späteren Zeitpunkt
    (Rückfall, falls die Analyse keinen eigenen Zeitpunkt liefert).
    """
    owner = ja._clean_owner(owner_id)
    current = ja._now(now)
    active_engine, dispose = ja._engine(engine)
    try:
        with active_engine.begin() as connection:
            ja._insert_notification(connection, owner, job["id"], run_id, day, title, body or "",
                                    "summary", current, notify_at=notify_at)
            notifications = connection.execute(text("""
                SELECT * FROM analysis_notifications
                WHERE owner_id = :owner AND job_id = CAST(:id AS uuid)
                ORDER BY created_at ASC
            """), {"owner": owner, "id": job["id"]}).mappings().all()
        ja._export_job(owner, data_root, job=job, notification_rows=notifications)
        return True
    finally:
        if dispose:
            active_engine.dispose()


def notify_reminder(owner_id, job, *, day, run_id, title, body, notify_at=None,
                    engine=None, now=None, data_root=None, any_job=False):
    """Eine Meldung je erkanntem Ereignis, mit eigenem Anzeigezeitpunkt.

    Deduplizierung: Gleicher Job + gleicher Quelltag + gleicher Text erzeugt nur
    eine Meldung — ein erneuter Lauf desselben Tages („Neu analysieren“) oder
    ein Modell, das eine Erinnerung aus dem Kontext wiederholt, darf nicht
    doppelt benachrichtigen. Der Zeitpunkt selbst zählt nicht zum Schlüssel,
    damit kleinere Abweichungen der Modellantwort keine Duplikate erzeugen.
    ``any_job`` prüft über alle Jobs des Nutzers: Feste Journal-Erinnerungen
    gehören keinem Job inhaltlich, sie hängen nur an einem — sie dürfen nicht
    einmal je Job erscheinen.

    Gibt ``True`` zurück, wenn eine Meldung angelegt wurde, ``False`` bei Duplikat.
    """
    owner = ja._clean_owner(owner_id)
    current = ja._now(now)
    clean_title = (title or "")[:ja.MAX_NAME]
    clean_body = (body or "")[:ja.MAX_NOTIFICATION_BODY]
    job_filter = "" if any_job else "AND job_id = CAST(:job AS uuid)"
    active_engine, dispose = ja._engine(engine)
    try:
        with active_engine.begin() as connection:
            duplicate = connection.execute(text(f"""
                SELECT 1 FROM analysis_notifications
                WHERE owner_id = :owner {job_filter}
                  AND run_date = :day AND title = :title AND body = :body
                LIMIT 1
            """), {"owner": owner, "job": job["id"], "day": day,
                   "title": clean_title, "body": clean_body}).first()
            if duplicate:
                return False
            ja._insert_notification(connection, owner, job["id"], run_id, day, clean_title,
                                    clean_body, "reminder", current, notify_at=notify_at)
            notifications = connection.execute(text("""
                SELECT * FROM analysis_notifications
                WHERE owner_id = :owner AND job_id = CAST(:id AS uuid)
                ORDER BY created_at ASC
            """), {"owner": owner, "id": job["id"]}).mappings().all()
        ja._export_job(owner, data_root, job=job, notification_rows=notifications)
        return True
    finally:
        if dispose:
            active_engine.dispose()


def recent_reminders(owner_id, *, start, end, engine=None):
    """Erinnerungen des Nutzers (alle Jobs), die zwischen ``start`` und ``end`` fällig werden.

    Grundlage der Wiederholungsprüfung: Ein Modell, das dieselbe Erinnerung
    an mehreren Tagen erneut meldet, soll sie nicht mehrfach zustellen.
    """
    owner = ja._clean_owner(owner_id)
    active_engine, dispose = ja._engine(engine)
    try:
        with active_engine.connect() as connection:
            rows = connection.execute(text("""
                SELECT run_date, title, body, notify_at FROM analysis_notifications
                WHERE owner_id = :owner AND kind = 'reminder'
                  AND notify_at BETWEEN :start AND :end
            """), {"owner": owner, "start": start, "end": end}).mappings().all()
        return [dict(row) for row in rows]
    finally:
        if dispose:
            active_engine.dispose()


def set_handoff_summary(owner_id, job_id, content, *, engine=None, now=None, data_root=None):
    """Die Zeitraum-Zusammenfassung als Uebergabe an den naechsten Lauf speichern.

    Wird **ueberschrieben** (nicht angehaengt) und auf ``MAX_HANDOFF`` gekappt,
    damit die Uebergabe nicht unbegrenzt waechst.
    """
    owner = ja._clean_owner(owner_id)
    value = (content or "").strip()[:ja.MAX_HANDOFF]
    current = ja._now(now)
    active_engine, dispose = ja._engine(engine)
    try:
        with active_engine.begin() as connection:
            result = connection.execute(text("""
                UPDATE analysis_jobs SET handoff_summary = :value, updated_at = :now
                WHERE owner_id = :owner AND id = CAST(:id AS uuid)
            """), {"value": value, "now": current, "owner": owner, "id": job_id})
            if result.rowcount == 0:
                raise ja.JobNotFound("Job nicht gefunden")
            job = connection.execute(text("""
                SELECT * FROM analysis_jobs WHERE id = CAST(:id AS uuid)
            """), {"id": job_id}).mappings().first()
        ja._export_job(owner, data_root, job=job)
        return value
    finally:
        if dispose:
            active_engine.dispose()


def store_run(owner_id, job, run, data_root):
    """Einzelnen Lauf in den Datei-Spiegel schreiben (best effort)."""
    root = ja._resolve_root(data_root)
    if root is None:
        return
    try:
        ja.store.write_run(owner_id, job["slug"], run["run_date"], _as_run(run), root=root)
    except (OSError, ValueError) as exc:
        module = ja._main()
        if module and hasattr(module, "logger"):
            module.logger.warning("Journal-Analyse-Lauf-Export fehlgeschlagen: %s", exc)


__all__ = [
    "list_runs", "get_run", "begin_run", "complete_run", "fail_run",
    "mark_run_stale", "notify_run", "notify_reminder", "recent_reminders",
    "set_handoff_summary", "store_run",
]