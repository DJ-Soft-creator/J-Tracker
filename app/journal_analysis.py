"""Fachlogik der Journal-Analyse-Verwaltung.

Eigenständiger Bereich (Entscheidung JA-01): wiederkehrende Analysen eigener
Journale mit Tageskette, Prompt-Vorlagen, Ausgabemodi und Benachrichtigungen.
Bewusst **ohne** Admin-Bindung und **ohne** Verbindung zum AI-Worker-, Ticket-
oder Host-Runner-System.

Die Datenbank ist Master (JA-02); nach jeder schreibenden Operation wird der
Datei-Spiegel über ``journal_analysis_store`` aktualisiert. Journale selbst
bleiben Datei-Master und werden von diesem Modul nie angefasst.

Kein HTTP hier — Request-Validierung und Serialisierung macht
``journal_analysis_routes.py``. Die Ausführung (lokaler LLM, Tageskette) liegt
im Sidecar (Etappe 3).
"""

import json
import sys
import uuid
from datetime import date, datetime, timedelta, timezone


from sqlalchemy import text

import db
import journal_analysis_sources as sources_module
import journal_analysis_store as store


DATE_RANGE_MODES = (
    "previous_day", "last_days", "current_week", "last_week", "current_month", "last_month", "custom",
)
# „Letzte N Tage“: Obergrenze wie die Fälligkeits-Rückschau der Zeitpläne.
MAX_RANGE_DAYS = 62
DEFAULT_RANGE_DAYS = 2
SCHEDULE_MODES = ("daily", "interval", "manual", "custom")
RUN_STATUSES = ("queued", "running", "completed", "failed", "cancelled")
NOTIFICATION_STATUSES = ("open", "ignored", "done", "taken")
NOTIFICATION_KINDS = ("analysis", "summary", "error", "reminder")
# Hinweise fallen nach so vielen Tagen (ab Fälligkeit bzw. Entstehung) aus den
# Listen im Schreiben-Tab und in der Verwaltung. Gelöscht wird nichts: das
# Protokoll zeigt sie weiter am jeweiligen Lauf.
NOTIFICATION_RETENTION_DAYS = 30

# Ändert sich eines dieser Felder, gelten bisherige Ergebnisse als veraltet
# (JA-06). Name, enabled und die Ausgabemodi zählen nicht dazu — sie ändern das
# Ergebnis nicht, nur seine Zustellung.
STALE_RELEVANT_FIELDS = (
    "date_range_mode", "custom_start", "custom_end", "provider_id", "model",
    "prompt_template_id", "extra_sources",
)

MAX_NAME = 200
MAX_SLUG = 80
MAX_MODEL = 200
MAX_PROVIDER = 200
MAX_TARGET = 500
MAX_SOURCE = 500
MAX_SUMMARY = 200000
MAX_STATE_BYTES = 200000
MAX_PROMPT_BODY = 50000
MAX_RESULT = 200000
MAX_ERROR = 4000
MAX_NOTIFICATION_BODY = 20000
# Obergrenze der Uebergabe-Zusammenfassung: wird bei jedem Lauf ueberschrieben,
# nicht angehaengt — so kann sie nicht unbegrenzt wachsen.
MAX_HANDOFF = 12000
# Reines Datum in einem benachrichtigten Ereignis: Anzeige zu dieser Uhrzeit
# (Server-Lokalzeit), damit nicht um Mitternacht benachrichtigt wird.
DEFAULT_NOTIFY_TIME = (9, 0)


class JournalAnalysisError(ValueError):
    """Ungültige Eingabe oder unzulässiger Zustandsübergang."""


class JobNotFound(JournalAnalysisError):
    pass


class PromptNotFound(JournalAnalysisError):
    pass


class RunNotFound(JournalAnalysisError):
    pass


class NotificationNotFound(JournalAnalysisError):
    pass


# ─── Infrastruktur ───────────────────────────────────────────────────


def _main():
    return sys.modules.get("main") or sys.modules.get("__main__")


def _engine(engine=None):
    if engine is not None:
        return engine, False
    from sqlalchemy import create_engine
    return create_engine(db.database_url(), pool_pre_ping=True), True


def _now(now=None):
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise JournalAnalysisError("Zeitpunkt muss eine Zeitzone enthalten")
    return now


def _local_timezone():
    """Zeitzone des Servers — naive Zeitstempel der Analyse sind dort lokal."""
    local = datetime.now().astimezone()
    return local.tzinfo or timezone.utc


def parse_notify_at(value, *, default_time=DEFAULT_NOTIFY_TIME):
    """Ein Anzeigezeitpunkt aus der Modellantwort; ``None`` bei ungültigem Wert.

    Akzeptiert ISO-Formate mit und ohne Uhrzeit (``2026-10-07T09:00``,
    ``2026-10-07 09:00``, ``2026-10-07``) sowie mit Sekunden/Offset. Ein reines
    Datum wird mit ``default_time`` zur Server-Lokalzeit ergänzt; naive Werte
    gelten als Server-Lokalzeit. Unbrauchbares (Text, Zukunftsfantasie, …)
    wird verworfen, statt den Lauf zu kippen.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        stamp = value
    else:
        text_value = str(value).strip().replace("Z", "+00:00")
        if len(text_value) <= 10:
            # Reines Datum ("2026-10-07") → zur Standard-Uhrzeit ergänzen.
            try:
                day = date.fromisoformat(text_value)
            except ValueError:
                return None
            hour, minute = default_time
            stamp = datetime(day.year, day.month, day.day, hour, minute)
        else:
            try:
                stamp = datetime.fromisoformat(text_value)
            except ValueError:
                return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=_local_timezone())
    return stamp


def _resolve_root(data_root):
    """Das Wurzelverzeichnis des Datei-Spiegels, oder ``None``.

    In reinen DB-Tests (Wegwerf-Postgres) gibt es kein ``DATA_DIR``; dann wird
    der Export bewusst übersprungen, statt die Fachlogik scheitern zu lassen.
    """
    if data_root is not None:
        return data_root
    module = _main()
    return getattr(module, "DATA_DIR", None) if module else None


def _slugify_or_fallback(name):
    slug = store.slugify(name)
    return slug or "job"


def _clean_text(value, field, *, max_length, required=False, allow_empty=False):
    if value is None:
        value = "" if allow_empty else ""
    if not isinstance(value, str):
        raise JournalAnalysisError(f"Ungültiges {field}")
    value = value.strip()
    if required and not value:
        raise JournalAnalysisError(f"{field} fehlt")
    if len(value) > max_length:
        raise JournalAnalysisError(f"{field} ist zu lang")
    return value


def _clean_owner(owner_id):
    if not isinstance(owner_id, str) or not owner_id.strip() or len(owner_id) > 240:
        raise JournalAnalysisError("Ungültiger Nutzer")
    return owner_id


def _validate_date(value, field):
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise JournalAnalysisError(f"Ungültiges {field}") from exc


def _validate_schedule_time(value):
    if value is None or value == "":
        return None
    text_value = str(value).strip()
    parts = text_value.split(":")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        raise JournalAnalysisError("Ungültige Uhrzeit")
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise JournalAnalysisError("Ungültige Uhrzeit")
    return f"{hour:02d}:{minute:02d}"


def _validate_schedule_rule(value):
    """Benutzerdefinierte Regel normalisieren: Wochentage 0–6 oder Monatstage 1–31."""
    if isinstance(value, str):
        try:
            value = json.loads(value) if value.strip() else None
        except ValueError as exc:
            raise JournalAnalysisError("Ungültige Zeitplan-Regel") from exc
    if not isinstance(value, dict):
        raise JournalAnalysisError("Für einen benutzerdefinierten Zeitplan Wochentage oder Monatstage angeben")
    kind = value.get("kind")
    if kind == "weekly":
        key, low, high = "weekdays", 0, 6
    elif kind == "monthly":
        key, low, high = "days", 1, 31
    else:
        raise JournalAnalysisError("Ungültige Zeitplan-Regel")
    raw = value.get(key)
    if not isinstance(raw, list):
        raise JournalAnalysisError("Ungültige Zeitplan-Regel")
    items = set()
    for item in raw:
        try:
            number = int(item)
        except (TypeError, ValueError) as exc:
            raise JournalAnalysisError("Ungültige Zeitplan-Regel") from exc
        if not (low <= number <= high):
            raise JournalAnalysisError("Ungültige Zeitplan-Regel")
        items.add(number)
    if not items:
        raise JournalAnalysisError(
            "Mindestens einen Wochentag wählen" if kind == "weekly" else "Mindestens einen Monatstag angeben")
    return {"kind": kind, key: sorted(items)}


def _validate_extra_sources(value):
    if value is None:
        return []
    if not isinstance(value, list):
        raise JournalAnalysisError("Quellen müssen eine Liste sein")
    cleaned = []
    for item in value:
        text_value = _clean_text(item, "Quelle", max_length=MAX_SOURCE, required=True)
        try:
            cleaned.append(sources_module.normalise_source(text_value))
        except ValueError as exc:
            raise JournalAnalysisError(f"Quelle „{text_value}“: {exc}") from exc
    return cleaned


def _json_value(value, default):
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return default


def _normalize(row):
    """DB-Werte JSON-/HTTP-tauglich machen: Datum und UUID nach String."""
    item = {}
    for key, value in dict(row).items():
        if isinstance(value, (datetime, date)):
            item[key] = value.isoformat()
        elif isinstance(value, uuid.UUID):
            item[key] = str(value)
        else:
            item[key] = value
    return item


def _rule_param(rule):
    return json.dumps(rule) if rule else None


def _as_job(row):
    job = _normalize(row)
    job["state"] = _json_value(job.get("state"), {})
    job["extra_sources"] = _json_value(job.get("extra_sources"), [])
    job["schedule_rule"] = _json_value(job.get("schedule_rule"), None)
    return job


def _as_notification(row):
    return _normalize(row)


def _public_job(row):
    job = _as_job(row)
    return job


def _export_job(owner_id, data_root, *, job=None, notification_rows=None, connection=None):
    """Den Datei-Spiegel eines Jobs aktualisieren (best effort, nie fatal).

    Ein fehlendes ``DATA_DIR`` (reine DB-Tests) überspringt den Export bewusst.
    """
    root = _resolve_root(data_root)
    if root is None or job is None:
        return
    slug = job.get("slug")
    if not slug:
        return
    try:
        store.write_job(owner_id, slug, _as_job(job), root=root)
        store.write_summary(owner_id, slug, job.get("summary") or "", root=root)
        store.write_state(owner_id, slug, _json_value(job.get("state"), {}), root=root)
        if notification_rows is not None:
            store.write_notifications(
                owner_id, slug, [_as_notification(row) for row in notification_rows], root=root)
    except (OSError, ValueError) as exc:
        module = _main()
        if module and hasattr(module, "logger"):
            module.logger.warning("Journal-Analyse-Export fehlgeschlagen: %s", exc)


def _unique_slug(connection, owner_id, base):
    candidate = base[:MAX_SLUG] or "job"
    suffix = 2
    while connection.execute(
        text("SELECT 1 FROM analysis_jobs WHERE owner_id = :owner AND slug = :slug"),
        {"owner": owner_id, "slug": candidate},
    ).first():
        tail = f"-{suffix}"
        candidate = (base[: MAX_SLUG - len(tail)] or "job") + tail
        suffix += 1
        if suffix > 9999:
            raise JournalAnalysisError("Kein freier Job-Name")
    return candidate


# ─── Prompt-Vorlagen ─────────────────────────────────────────────────


def list_prompt_templates(owner_id, *, engine=None):
    owner = _clean_owner(owner_id)
    active_engine, dispose = _engine(engine)
    try:
        with active_engine.connect() as connection:
            rows = connection.execute(text("""
                SELECT id, owner_id, name, body, version, created_at, updated_at
                FROM analysis_prompt_templates WHERE owner_id = :owner
                ORDER BY name ASC
            """), {"owner": owner}).mappings().all()
        return [dict(row) for row in rows]
    finally:
        if dispose:
            active_engine.dispose()


def create_prompt_template(owner_id, name, body, *, engine=None, now=None):
    owner = _clean_owner(owner_id)
    clean_name = _clean_text(name, "Name", max_length=MAX_NAME, required=True)
    clean_body = _clean_text(body, "Prompt", max_length=MAX_PROMPT_BODY, required=True)
    current = _now(now)
    template_id = str(uuid.uuid4())
    active_engine, dispose = _engine(engine)
    try:
        with active_engine.begin() as connection:
            connection.execute(text("""
                INSERT INTO analysis_prompt_templates (id, owner_id, name, body, version, created_at, updated_at)
                VALUES (CAST(:id AS uuid), :owner, :name, :body, 1, :now, :now)
            """), {"id": template_id, "owner": owner, "name": clean_name,
                   "body": clean_body, "now": current})
        return get_prompt_template(owner, template_id, engine=engine)
    finally:
        if dispose:
            active_engine.dispose()


def get_prompt_template(owner_id, template_id, *, engine=None):
    owner = _clean_owner(owner_id)
    active_engine, dispose = _engine(engine)
    try:
        with active_engine.connect() as connection:
            row = connection.execute(text("""
                SELECT id, owner_id, name, body, version, created_at, updated_at
                FROM analysis_prompt_templates
                WHERE owner_id = :owner AND id = CAST(:id AS uuid)
            """), {"owner": owner, "id": template_id}).mappings().first()
        if row is None:
            raise PromptNotFound("Prompt-Vorlage nicht gefunden")
        return dict(row)
    finally:
        if dispose:
            active_engine.dispose()


def update_prompt_template(owner_id, template_id, *, name=None, body=None, engine=None, now=None,
                           data_root=None):
    """Vorlage ändern; eine Body-Änderung hebt die Version und markiert Jobs veraltet."""
    owner = _clean_owner(owner_id)
    current = _now(now)
    active_engine, dispose = _engine(engine)
    try:
        with active_engine.begin() as connection:
            existing = connection.execute(text("""
                SELECT id, name, body, version FROM analysis_prompt_templates
                WHERE owner_id = :owner AND id = CAST(:id AS uuid)
            """), {"owner": owner, "id": template_id}).mappings().first()
            if existing is None:
                raise PromptNotFound("Prompt-Vorlage nicht gefunden")
            new_name = _clean_text(name, "Name", max_length=MAX_NAME, required=True) if name is not None else existing["name"]
            new_body = _clean_text(body, "Prompt", max_length=MAX_PROMPT_BODY, required=True) if body is not None else existing["body"]
            body_changed = new_body != existing["body"]
            new_version = existing["version"] + 1 if body_changed else existing["version"]
            connection.execute(text("""
                UPDATE analysis_prompt_templates
                SET name = :name, body = :body, version = :version, updated_at = :now
                WHERE owner_id = :owner AND id = CAST(:id AS uuid)
            """), {"name": new_name, "body": new_body, "version": new_version,
                   "now": current, "owner": owner, "id": template_id})
            if body_changed:
                affected = connection.execute(text("""
                    SELECT id FROM analysis_jobs
                    WHERE owner_id = :owner AND prompt_template_id = CAST(:id AS uuid)
                """), {"owner": owner, "id": template_id}).scalars().all()
                for job_id in affected:
                    _bump_job_version_and_stale(connection, owner, str(job_id), current,
                                                reason="prompt_changed")
        return get_prompt_template(owner, template_id, engine=engine)
    finally:
        if dispose:
            active_engine.dispose()


def delete_prompt_template(owner_id, template_id, *, engine=None):
    owner = _clean_owner(owner_id)
    active_engine, dispose = _engine(engine)
    try:
        with active_engine.begin() as connection:
            in_use = connection.execute(text("""
                SELECT 1 FROM analysis_jobs
                WHERE owner_id = :owner AND prompt_template_id = CAST(:id AS uuid) LIMIT 1
            """), {"owner": owner, "id": template_id}).first()
            if in_use:
                raise JournalAnalysisError("Vorlage wird von einem Job verwendet")
            result = connection.execute(text("""
                DELETE FROM analysis_prompt_templates
                WHERE owner_id = :owner AND id = CAST(:id AS uuid)
            """), {"owner": owner, "id": template_id})
            if result.rowcount == 0:
                raise PromptNotFound("Prompt-Vorlage nicht gefunden")
        return True
    finally:
        if dispose:
            active_engine.dispose()


# ─── Jobs ────────────────────────────────────────────────────────────


def list_jobs(owner_id, *, engine=None):
    owner = _clean_owner(owner_id)
    active_engine, dispose = _engine(engine)
    try:
        with active_engine.connect() as connection:
            rows = connection.execute(text("""
                SELECT * FROM analysis_jobs WHERE owner_id = :owner ORDER BY name ASC
            """), {"owner": owner}).mappings().all()
        return [_as_job(row) for row in rows]
    finally:
        if dispose:
            active_engine.dispose()


def list_enabled_jobs(*, engine=None):
    """Alle aktiven Jobs aller Nutzer — die Arbeitsliste des Sidecars."""
    active_engine, dispose = _engine(engine)
    try:
        with active_engine.connect() as connection:
            rows = connection.execute(text("""
                SELECT * FROM analysis_jobs WHERE enabled = true ORDER BY owner_id, name
            """)).mappings().all()
        return [_as_job(row) for row in rows]
    finally:
        if dispose:
            active_engine.dispose()


def get_job(owner_id, job_id, *, engine=None):
    owner = _clean_owner(owner_id)
    active_engine, dispose = _engine(engine)
    try:
        with active_engine.connect() as connection:
            row = connection.execute(text("""
                SELECT * FROM analysis_jobs WHERE owner_id = :owner AND id = CAST(:id AS uuid)
            """), {"owner": owner, "id": job_id}).mappings().first()
        if row is None:
            raise JobNotFound("Job nicht gefunden")
        return _as_job(row)
    finally:
        if dispose:
            active_engine.dispose()


def _validate_job_values(owner, values, *, engine, require_all):
    """Felder prüfen und normalisieren. ``require_all`` gilt beim Anlegen."""
    result = {}
    if require_all or "name" in values:
        result["name"] = _clean_text(values.get("name"), "Name", max_length=MAX_NAME, required=True)
    if require_all or "schedule_mode" in values:
        mode = values.get("schedule_mode", "daily")
        if mode not in SCHEDULE_MODES:
            raise JournalAnalysisError("Ungültiger Zeitplan-Modus")
        result["schedule_mode"] = mode
    if require_all or "schedule_time" in values:
        result["schedule_time"] = _validate_schedule_time(values.get("schedule_time"))
    if require_all or "schedule_interval_hours" in values:
        interval = values.get("schedule_interval_hours")
        if interval is None or interval == "":
            result["schedule_interval_hours"] = None
        else:
            try:
                interval = int(interval)
            except (TypeError, ValueError) as exc:
                raise JournalAnalysisError("Ungültiges Intervall") from exc
            if interval < 1 or interval > 24 * 30:
                raise JournalAnalysisError("Ungültiges Intervall")
            result["schedule_interval_hours"] = interval
    if require_all or "date_range_mode" in values:
        mode = values.get("date_range_mode", "previous_day")
        if mode not in DATE_RANGE_MODES:
            raise JournalAnalysisError("Ungültiger Zeitraum-Modus")
        result["date_range_mode"] = mode
    if require_all or "range_days" in values:
        days = values.get("range_days")
        if days in (None, ""):
            result["range_days"] = None
        else:
            try:
                days = int(days)
            except (TypeError, ValueError) as exc:
                raise JournalAnalysisError("Ungültige Anzahl Tage") from exc
            if days < 1 or days > MAX_RANGE_DAYS:
                raise JournalAnalysisError(f"Anzahl Tage muss zwischen 1 und {MAX_RANGE_DAYS} liegen")
            result["range_days"] = days
    if require_all or "custom_start" in values:
        result["custom_start"] = _validate_date(values.get("custom_start"), "Startdatum")
    if require_all or "custom_end" in values:
        result["custom_end"] = _validate_date(values.get("custom_end"), "Enddatum")
    if require_all or "provider_id" in values:
        result["provider_id"] = _clean_text(values.get("provider_id", ""), "Anbieter",
                                            max_length=MAX_PROVIDER)
    if require_all or "model" in values:
        result["model"] = _clean_text(values.get("model", ""), "Modell", max_length=MAX_MODEL)
    if require_all or "prompt_template_id" in values:
        template_id = values.get("prompt_template_id")
        if template_id in (None, ""):
            result["prompt_template_id"] = None
        else:
            try:
                uuid.UUID(str(template_id))
            except (ValueError, TypeError) as exc:
                raise JournalAnalysisError("Ungültige Prompt-Vorlage") from exc
            template = get_prompt_template(owner, template_id, engine=engine)
            result["prompt_template_id"] = template["id"]
    for flag, default in (("output_notification", True), ("output_journal", False),
                          ("output_family", False), ("output_run_summary", True),
                          ("carry_summary", False), ("output_notes", False)):
        if require_all or flag in values:
            result[flag] = bool(values.get(flag, default))
    if require_all or "family_target" in values:
        target = values.get("family_target")
        result["family_target"] = _clean_text(target, "Familien-Ziel", max_length=MAX_TARGET) if target else None
    if require_all or "notes_target" in values:
        target = values.get("notes_target")
        result["notes_target"] = _clean_text(target, "Notiz-Ziel", max_length=MAX_TARGET) if target else None
    if require_all or "extra_sources" in values:
        result["extra_sources"] = _validate_extra_sources(values.get("extra_sources"))
    if require_all or "enabled" in values:
        result["enabled"] = bool(values.get("enabled", True))

    # Modus-abhängige Pflichtfelder nach dem Zusammenführen prüfen.
    mode = result.get("date_range_mode")
    if mode == "last_days" and not result.get("range_days"):
        result["range_days"] = DEFAULT_RANGE_DAYS
    if mode == "custom":
        if not result.get("custom_start") or not result.get("custom_end"):
            raise JournalAnalysisError("Für einen eigenen Zeitraum Start und Ende angeben")
        if result["custom_start"] > result["custom_end"]:
            raise JournalAnalysisError("Der Zeitraum ist verdreht")
    if result.get("output_family") and not result.get("family_target"):
        raise JournalAnalysisError("Für die Familien-Ausgabe ein Ziel angeben")
    schedule = result.get("schedule_mode")
    if schedule == "daily" and not result.get("schedule_time"):
        result["schedule_time"] = "06:00"
    if schedule == "interval" and not result.get("schedule_interval_hours"):
        raise JournalAnalysisError("Für einen Takt die Stunden angeben")
    if schedule == "custom":
        if not result.get("schedule_time"):
            result["schedule_time"] = "06:00"
        result["schedule_rule"] = _validate_schedule_rule(values.get("schedule_rule"))
    elif schedule is not None:
        result["schedule_rule"] = None
    return result


def create_job(owner_id, values, *, engine=None, now=None, data_root=None):
    owner = _clean_owner(owner_id)
    if not isinstance(values, dict):
        raise JournalAnalysisError("Ungültige Job-Daten")
    current = _now(now)
    active_engine, dispose = _engine(engine)
    try:
        with active_engine.begin() as connection:
            # _validate_job_values braucht teils eine Verbindung (Vorlage) —
            # deshalb hier mit derselben Engine arbeiten.
            clean = _validate_job_values(owner, values, engine=active_engine, require_all=True)
            if "slug" in values and values["slug"] not in (None, ""):
                base = _clean_text(values["slug"], "Kürzel", max_length=MAX_SLUG, required=True)
                slug = _unique_slug(connection, owner, store.slugify(base) or base)
            else:
                slug = _unique_slug(connection, owner, _slugify_or_fallback(clean["name"]))
            job_id = str(uuid.uuid4())
            connection.execute(text("""
                INSERT INTO analysis_jobs (
                    id, owner_id, slug, name, enabled, schedule_mode, schedule_time,
                    schedule_interval_hours, schedule_rule, date_range_mode, range_days, custom_start, custom_end,
                    provider_id, model, prompt_template_id, output_notification, output_journal,
                    output_family, family_target, output_run_summary, carry_summary, output_notes,
                    notes_target, extra_sources, summary, state, job_version,
                    last_run_date, created_at, updated_at
                ) VALUES (
                    CAST(:id AS uuid), :owner, :slug, :name, :enabled, :schedule_mode, :schedule_time,
                    :schedule_interval_hours, CAST(:schedule_rule AS jsonb), :date_range_mode, :range_days, :custom_start, :custom_end,
                    :provider_id, :model, CAST(:prompt_template_id AS uuid), :output_notification,
                    :output_journal, :output_family, :family_target, :output_run_summary,
                    :carry_summary, :output_notes, :notes_target, CAST(:extra_sources AS jsonb),
                    '', '{}'::jsonb, 1, NULL, :now, :now
                )
            """), {
                "id": job_id, "owner": owner, "slug": slug, "name": clean["name"],
                "enabled": clean["enabled"], "schedule_mode": clean["schedule_mode"],
                "schedule_time": clean["schedule_time"],
                "schedule_interval_hours": clean["schedule_interval_hours"],
                "schedule_rule": _rule_param(clean.get("schedule_rule")),
                "date_range_mode": clean["date_range_mode"], "range_days": clean.get("range_days"),
                "custom_start": clean["custom_start"],
                "custom_end": clean["custom_end"], "provider_id": clean["provider_id"],
                "model": clean["model"], "prompt_template_id": clean["prompt_template_id"],
                "output_notification": clean["output_notification"],
                "output_journal": clean["output_journal"], "output_family": clean["output_family"],
                "family_target": clean["family_target"],
                "output_run_summary": clean["output_run_summary"],
                "carry_summary": clean["carry_summary"], "output_notes": clean["output_notes"],
                "notes_target": clean["notes_target"],
                "extra_sources": json.dumps(clean["extra_sources"]),
                "now": current,
            })
        job = get_job(owner, job_id, engine=engine)
        _export_job(owner, data_root, job=job, notification_rows=[])
        return job
    finally:
        if dispose:
            active_engine.dispose()


def update_job(owner_id, job_id, values, *, engine=None, now=None, data_root=None):
    owner = _clean_owner(owner_id)
    if not isinstance(values, dict):
        raise JournalAnalysisError("Ungültige Job-Daten")
    current = _now(now)
    active_engine, dispose = _engine(engine)
    try:
        with active_engine.begin() as connection:
            existing = connection.execute(text("""
                SELECT * FROM analysis_jobs WHERE owner_id = :owner AND id = CAST(:id AS uuid)
            """), {"owner": owner, "id": job_id}).mappings().first()
            if existing is None:
                raise JobNotFound("Job nicht gefunden")
            current_job = _as_job(existing)
            # Fehlende Felder aus dem Bestand auffüllen, damit die
            # modus-abhängigen Pflichtprüfungen greifen.
            merged = {key: current_job.get(key) for key in (
                "name", "schedule_mode", "schedule_time", "schedule_interval_hours",
                "schedule_rule", "date_range_mode", "range_days", "custom_start", "custom_end", "provider_id", "model",
                "prompt_template_id", "output_notification", "output_journal", "output_family",
                "family_target", "output_run_summary", "carry_summary", "output_notes",
                "notes_target", "extra_sources", "enabled")}
            merged.update(values)
            clean = _validate_job_values(owner, merged, engine=active_engine, require_all=False)
            stale = any(
                _field_changed(current_job.get(field), clean.get(field))
                for field in STALE_RELEVANT_FIELDS if field in clean
            )
            version = current_job["job_version"] + 1 if stale else current_job["job_version"]
            connection.execute(text("""
                UPDATE analysis_jobs SET
                    name = :name, enabled = :enabled, schedule_mode = :schedule_mode,
                    schedule_time = :schedule_time, schedule_interval_hours = :schedule_interval_hours,
                    schedule_rule = CAST(:schedule_rule AS jsonb), date_range_mode = :date_range_mode, range_days = :range_days, custom_start = :custom_start,
                    custom_end = :custom_end, provider_id = :provider_id, model = :model,
                    prompt_template_id = CAST(:prompt_template_id AS uuid),
                    output_notification = :output_notification, output_journal = :output_journal,
                    output_family = :output_family, family_target = :family_target,
                    output_run_summary = :output_run_summary, carry_summary = :carry_summary,
                    output_notes = :output_notes, notes_target = :notes_target,
                    extra_sources = CAST(:extra_sources AS jsonb), job_version = :version,
                    updated_at = :now
                WHERE owner_id = :owner AND id = CAST(:id AS uuid)
            """), {
                "name": clean["name"], "enabled": clean["enabled"],
                "schedule_mode": clean["schedule_mode"], "schedule_time": clean["schedule_time"],
                "schedule_interval_hours": clean["schedule_interval_hours"],
                "schedule_rule": _rule_param(clean.get("schedule_rule")),
                "date_range_mode": clean["date_range_mode"], "range_days": clean.get("range_days"),
                "custom_start": clean["custom_start"],
                "custom_end": clean["custom_end"], "provider_id": clean["provider_id"],
                "model": clean["model"], "prompt_template_id": clean["prompt_template_id"],
                "output_notification": clean["output_notification"],
                "output_journal": clean["output_journal"], "output_family": clean["output_family"],
                "family_target": clean["family_target"],
                "output_run_summary": clean["output_run_summary"],
                "carry_summary": clean["carry_summary"], "output_notes": clean["output_notes"],
                "notes_target": clean["notes_target"],
                "extra_sources": json.dumps(clean["extra_sources"]),
                "version": version, "now": current, "owner": owner, "id": job_id,
            })
            if stale:
                connection.execute(text("""
                    UPDATE analysis_runs SET stale = true, stale_reason = :reason, updated_at = :now
                    WHERE job_id = CAST(:id AS uuid) AND stale = false
                """), {"reason": "job_changed", "now": current, "id": job_id})
            job = _as_job(connection.execute(text("""
                SELECT * FROM analysis_jobs WHERE owner_id = :owner AND id = CAST(:id AS uuid)
            """), {"owner": owner, "id": job_id}).mappings().first())
            notifications = connection.execute(text("""
                SELECT * FROM analysis_notifications WHERE owner_id = :owner AND job_id = CAST(:id AS uuid)
                ORDER BY created_at ASC
            """), {"owner": owner, "id": job_id}).mappings().all()
        _export_job(owner, data_root, job=job, notification_rows=notifications)
        return job
    finally:
        if dispose:
            active_engine.dispose()


def delete_job(owner_id, job_id, *, engine=None):
    """Einen Job samt Verlauf und Benachrichtigungen löschen (bewusste Aktion)."""
    owner = _clean_owner(owner_id)
    active_engine, dispose = _engine(engine)
    try:
        with active_engine.begin() as connection:
            existing = connection.execute(text("""
                SELECT id, slug FROM analysis_jobs WHERE owner_id = :owner AND id = CAST(:id AS uuid)
            """), {"owner": owner, "id": job_id}).mappings().first()
            if existing is None:
                raise JobNotFound("Job nicht gefunden")
            connection.execute(text("""
                DELETE FROM analysis_run_events WHERE run_id IN
                    (SELECT id FROM analysis_runs WHERE job_id = CAST(:id AS uuid))
            """), {"id": job_id})
            connection.execute(text("DELETE FROM analysis_notifications WHERE job_id = CAST(:id AS uuid)"),
                               {"id": job_id})
            connection.execute(text("DELETE FROM analysis_runs WHERE job_id = CAST(:id AS uuid)"),
                               {"id": job_id})
            connection.execute(text("DELETE FROM analysis_jobs WHERE id = CAST(:id AS uuid)"),
                               {"id": job_id})
        return existing["slug"]
    finally:
        if dispose:
            active_engine.dispose()


def _field_changed(old, new):
    if isinstance(old, date):
        old = old.isoformat()
    if isinstance(new, date):
        new = new.isoformat()
    return old != new


def _bump_job_version_and_stale(connection, owner, job_id, now, *, reason):
    connection.execute(text("""
        UPDATE analysis_jobs SET job_version = job_version + 1, updated_at = :now
        WHERE owner_id = :owner AND id = CAST(:id AS uuid)
    """), {"now": now, "owner": owner, "id": job_id})
    connection.execute(text("""
        UPDATE analysis_runs SET stale = true, stale_reason = :reason, updated_at = :now
        WHERE job_id = CAST(:id AS uuid) AND stale = false
    """), {"reason": reason, "now": now, "id": job_id})


# ─── Benachrichtigungen ──────────────────────────────────────────────


def _insert_notification(connection, owner, job_id, run_id, day, title, body, kind, now,
                         notify_at=None):
    kind = kind if kind in NOTIFICATION_KINDS else "analysis"
    connection.execute(text("""
        INSERT INTO analysis_notifications (
            id, owner_id, job_id, run_id, run_date, title, body, kind, status,
            notify_at, created_at, updated_at
        ) VALUES (
            CAST(:id AS uuid), :owner, CAST(:job AS uuid), CAST(:run AS uuid), :day,
            :title, :body, :kind, 'open', :notify_at, :now, :now
        )
    """), {"id": str(uuid.uuid4()), "owner": owner, "job": job_id,
           "run": run_id, "day": day,
           "title": title[:MAX_NAME], "body": (body or "")[:MAX_NOTIFICATION_BODY],
           "kind": kind, "notify_at": notify_at, "now": now})


def list_notifications(owner_id, *, status=None, statuses=None, limit=200, engine=None,
                       due_only=False, now=None, max_age_days=None):
    """Benachrichtigungen auflisten.

    ``status`` wählt genau einen Zustand, ``statuses`` mehrere (z. B. offen und
    ignoriert für die aktive Ansicht). Beide sind optional; ohne Angabe wird
    alles zurückgegeben. ``due_only`` blendet geplante Meldungen aus, deren
    ``notify_at`` noch in der Zukunft liegt — der Schreiben-Tab zeigt nur, was
    jetzt relevant ist; die Verwaltung sieht auch „Geplant am …“.
    ``max_age_days`` blendet Meldungen aus, die länger als so viele Tage fällig
    bzw. vorhanden sind (``NOTIFICATION_RETENTION_DAYS`` für die Listen).
    """
    owner = _clean_owner(owner_id)
    try:
        limit = max(1, min(int(limit), 1000))
    except (TypeError, ValueError):
        limit = 200
    wanted = [item for item in (statuses or []) if item in NOTIFICATION_STATUSES]
    if not wanted and status in NOTIFICATION_STATUSES:
        wanted = [status]
    if not wanted:
        wanted = list(NOTIFICATION_STATUSES)
    current = _now(now)
    params = {"owner": owner, "statuses": wanted, "limit": limit, "now": current}
    where = ["owner_id = :owner", "status = ANY(CAST(:statuses AS text[]))"]
    if due_only:
        where.append("(notify_at IS NULL OR notify_at <= :now)")
    if max_age_days:
        where.append("COALESCE(notify_at, created_at) >= :cutoff")
        params["cutoff"] = current - timedelta(days=int(max_age_days))
    order = "COALESCE(notify_at, created_at)" if due_only else "created_at"
    active_engine, dispose = _engine(engine)
    try:
        with active_engine.connect() as connection:
            rows = connection.execute(text(
                f"SELECT * FROM analysis_notifications WHERE {' AND '.join(where)} "
                f"ORDER BY {order} DESC LIMIT :limit"
            ), params).mappings().all()
        return [_as_notification(row) for row in rows]
    finally:
        if dispose:
            active_engine.dispose()


def close_due_notifications(owner_id, *, engine=None, now=None, data_root=None):
    """Alle fälligen offenen/ignorierten Hinweise auf einmal abhaken.

    „Alle schließen“ im Schreiben-Tab: geplante (noch nicht fällige) bleiben
    unberührt. Gibt die Anzahl geschlossener Hinweise zurück.
    """
    owner = _clean_owner(owner_id)
    current = _now(now)
    active_engine, dispose = _engine(engine)
    try:
        with active_engine.begin() as connection:
            closed = connection.execute(text("""
                UPDATE analysis_notifications SET status = 'done', updated_at = :now
                WHERE owner_id = :owner AND status IN ('open', 'ignored')
                  AND (notify_at IS NULL OR notify_at <= :now)
                RETURNING job_id
            """), {"owner": owner, "now": current}).mappings().all()
            job_ids = sorted({str(row["job_id"]) for row in closed if row["job_id"]})
            exports = []
            for job_id in job_ids:
                job = connection.execute(text("""
                    SELECT * FROM analysis_jobs WHERE id = CAST(:job AS uuid)
                """), {"job": job_id}).mappings().first()
                if job is None:
                    continue
                notifications = connection.execute(text("""
                    SELECT * FROM analysis_notifications
                    WHERE owner_id = :owner AND job_id = CAST(:id AS uuid)
                    ORDER BY created_at ASC
                """), {"owner": owner, "id": job_id}).mappings().all()
                exports.append((job, notifications))
        for job, notifications in exports:
            _export_job(owner, data_root, job=_as_job(job), notification_rows=notifications)
        return len(closed)
    finally:
        if dispose:
            active_engine.dispose()


def set_notification_status(owner_id, notification_id, status, *, engine=None, now=None,
                            data_root=None):
    owner = _clean_owner(owner_id)
    if status not in NOTIFICATION_STATUSES:
        raise JournalAnalysisError("Ungültiger Benachrichtigungs-Status")
    current = _now(now)
    active_engine, dispose = _engine(engine)
    try:
        with active_engine.begin() as connection:
            existing = connection.execute(text("""
                SELECT * FROM analysis_notifications
                WHERE owner_id = :owner AND id = CAST(:id AS uuid)
            """), {"owner": owner, "id": notification_id}).mappings().first()
            if existing is None:
                raise NotificationNotFound("Benachrichtigung nicht gefunden")
            connection.execute(text("""
                UPDATE analysis_notifications SET status = :status, updated_at = :now
                WHERE owner_id = :owner AND id = CAST(:id AS uuid)
            """), {"status": status, "now": current, "owner": owner, "id": notification_id})
            row = connection.execute(text("""
                SELECT * FROM analysis_notifications WHERE id = CAST(:id AS uuid)
            """), {"id": notification_id}).mappings().first()
            job = connection.execute(text("""
                SELECT * FROM analysis_jobs WHERE id = CAST(:job AS uuid)
            """), {"job": existing["job_id"]}).mappings().first()
            notifications = connection.execute(text("""
                SELECT * FROM analysis_notifications WHERE owner_id = :owner AND job_id = CAST(:id AS uuid)
                ORDER BY created_at ASC
            """), {"owner": owner, "id": existing["job_id"]}).mappings().all()
        if job is not None:
            _export_job(owner, data_root, job=_as_job(job), notification_rows=notifications)
        return _as_notification(row)
    finally:
        if dispose:
            active_engine.dispose()


__all__ = [
    "JournalAnalysisError", "JobNotFound", "PromptNotFound", "RunNotFound",
    "NotificationNotFound", "DATE_RANGE_MODES", "SCHEDULE_MODES",
    "list_prompt_templates", "create_prompt_template", "get_prompt_template",
    "update_prompt_template", "delete_prompt_template",
    "list_jobs", "list_enabled_jobs", "get_job", "create_job", "update_job", "delete_job",
    "list_notifications", "set_notification_status", "close_due_notifications",
    "NOTIFICATION_RETENTION_DAYS", "parse_notify_at",
]