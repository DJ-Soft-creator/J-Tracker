"""Der generische Analyse-Worker: Tageskette über einen Zeitraum.

Ein Job wird **Tag für Tag** analysiert (JA-05). Jeder Tag ist eine eigene
Session; die fortgeschriebene ``summary`` und der strukturierte ``state`` gehen
als Kontext in den Folgetag. So bleibt das Kontextfenster begrenzt, ohne
übergreifende Erkenntnisse zu verlieren.

Der Worker kennt weder HTTP noch einen konkreten Anbieter: ``call_ai`` und
``provider`` kommen vom Sidecar. Dadurch ist er ohne Netz testbar.

Der Modellvertrag ist bewusst streng: Die Antwort soll ein JSON-Objekt mit
``summary``, ``state`` und ``result`` sein. Lokale Modelle halten sich nicht
immer daran — deshalb gibt es einen robusten Rückfall (die ganze Antwort wird
als Ergebnis und Zusammenfassung genommen, der bisherige State bleibt).
"""

import fcntl
import json
import os
import re
from datetime import date, datetime, timedelta

import journal_analysis as ja
import journal_reminders as jr
import journal_analysis_dates as dates
import journal_analysis_output as output
import journal_analysis_runs as runs
import journal_analysis_sources as sources_module
import journal_analysis_store as store


DEFAULT_SYSTEM_PROMPT = (
    "Du bist ein nüchterner Journal-Analyst. Du liest die Einträge eines Tages "
    "und hältst fest, was für die Zukunft relevant ist: offene Aufgaben, "
    "Themen, die weiterlaufen, und künftige Erinnerungen. Du erfindest nichts "
    "dazu und bewertest nicht. Die Zusammenfassung ist kumulativ: Sie führt "
    "die Erkenntnisse aller bisherigen Tage weiter und darf frühere Einträge "
    "nicht verlieren; nur wenn ein Tag nichts Neues bringt, bleibt sie "
    "inhaltlich stehen.\n"
    "Benachrichtigungspflichtige Ereignisse meldest du über `reminders`: für "
    "jede künftige Aktion oder Erinnerung, die im analysierten Journaltag "
    "(siehe \"## Tag\" im Prompt) genannt wird, genau ein Objekt "
    "{\"text\": \"...\", \"at\": \"YYYY-MM-DDTHH:MM\"}. `at` ist ein "
    "absoluter lokaler Zeitstempel, gerechnet vom Datum DES JOURNALTAGS, "
    "nicht vom aktuellen Datum: steht im Eintrag \"in einer Woche\", so liegt "
    "`at` eine Woche nach dem Journaltag. Nimm keine Erinnerungen erneut auf, "
    "die schon im übergebenen Zustand oder in der bisherigen Zusammenfassung "
    "stehen — nur wirklich neue aus dem aktuellen Tag. Ohne konkreten Termin "
    "bleibt `reminders` leer. Das oberste `reminders` ist die Benachrichtigung; "
    "`state.reminders` ist nur dein Gedächtnis für die nächsten Tage und löst "
    "nichts aus — trage neue Erinnerungen deshalb immer oben ein. Führe "
    "`state.reminders` als Objekte {\"text\": \"...\", \"at\": \"YYYY-MM-DDTHH:MM\"} "
    "und streiche dort Erinnerungen, deren Zeitpunkt vor dem Journaltag liegt. "
    "Wochentage und relative Angaben („nächste Woche Mittwoch“) liest du im "
    "Abschnitt „## Kalender“ nach, statt sie selbst auszurechnen. Erinnerungen "
    "unter „## Bereits angelegte Erinnerungen“ sind schon zugestellt — melde "
    "sie nicht erneut.\n"
    "Antworte ausschließlich mit einem JSON-Objekt der Form "
    "{\"summary\": \"...\", \"state\": {\"open_tasks\": [], "
    "\"themes\": [], \"reminders\": []}, \"result\": \"...\", "
    "\"reminders\": [{\"text\": \"...\", \"at\": \"YYYY-MM-DDTHH:MM\"}]}."
)

SUMMARY_SYSTEM_PROMPT = (
    "Du fasst eine mehrtägige Journal-Analyse zu EINER Zusammenfassung zusammen. "
    "Du lieferst keine Tagesauflistung und wiederholst keine Tagesabschnitte, "
    "sondern ziehst die übergreifenden offenen Aufgaben, Themen und künftigen "
    "Erinnerungen zusammen. Du erfindest nichts dazu. Antworte mit dem "
    "Zusammenfassungstext, ohne Vorrede."
)

DEFAULT_TEMPLATE = (
    "Analysiere den Journal-Tag {day}.\n\n"
    "## Bisherige Zusammenfassung (aus den Vortagen)\n{summary}\n\n"
    "## Bisheriger strukturierter Zustand\n{state}\n\n"
    "## Quellen des Tages\n{sources}\n\n"
    "Führe die Zusammenfassung fort und aktualisiere den Zustand. "
    "Antworte nur als JSON."
)

MAX_RESPONSE_CHARS = 200000
# Reasoning-Modelle verbrauchen einen Teil des Budgets fuers Denken; ein zu
# knapper Deckel liefert dann leeren content. Der Anbieter darf deshalb sein
# eigenes max_tokens mitbringen, sonst gilt dieser Standard.
DEFAULT_MAX_TOKENS = 3000
# Ergebnis eines Tages ohne eigenen Text: abgeschlossen, aber ohne Modellaufruf.
EMPTY_DAY_RESULT = "Keine Einträge an diesem Tag – ohne Modellaufruf abgeschlossen."

# Die Analyse streamt (``stream`` in ``_call_ai_api``). Dadurch ist
# ``AI_REQUEST_TIMEOUT`` keine Gesamtdauer mehr, sondern die Leerlaufgrenze:
# so lange darf der Anbieter gar nichts schicken — das deckt das Einlesen des
# grossen Prompts vor dem ersten Token ab. Ein Modell, das stetig erzeugt,
# laeuft bis ``AI_MAX_DURATION`` weiter. Vorher (ohne Streaming) kam das
# erste Byte erst mit der fertigen Antwort, und ein langsames
# Reasoning-Modell scheiterte nach 900s mit „timed out“, obwohl es arbeitete.
AI_REQUEST_TIMEOUT = 900
AI_MAX_DURATION = 3600
_FENCE_RE = re.compile(r"^```[a-zA-Z0-9_-]*\s*|\s*```$")


def _ai_timeout():
    """Zeitlimit je Modellaufruf (Sekunden).

    Über ``JOURNAL_ANALYSIS_AI_TIMEOUT`` aenderbar; ungueltige oder zu kleine
    Werte fallen auf ``AI_REQUEST_TIMEOUT`` zurueck.
    """
    try:
        value = int(os.environ.get("JOURNAL_ANALYSIS_AI_TIMEOUT", ""))
    except (TypeError, ValueError):
        return AI_REQUEST_TIMEOUT
    return value if value >= 30 else AI_REQUEST_TIMEOUT


def _ai_max_duration():
    """Gesamtdauer je gestreamtem Modellaufruf (Sekunden).

    Über ``JOURNAL_ANALYSIS_AI_MAX_DURATION`` aenderbar; nie kuerzer als die
    Leerlaufgrenze, sonst wuerde ein Lauf schon vor dem ersten Token enden.
    """
    try:
        value = int(os.environ.get("JOURNAL_ANALYSIS_AI_MAX_DURATION", ""))
    except (TypeError, ValueError):
        value = AI_MAX_DURATION
    return max(value, _ai_timeout())


def _ai_call_options(**extra):
    """Gemeinsame Aufrufoptionen der Analyse: streamen, Leerlauf, Gesamtdauer."""
    return {"temperature": 0.2, "stream": True, "timeout": _ai_timeout(),
            "max_duration": _ai_max_duration(), **extra}


class JobBusyError(RuntimeError):
    """Für denselben Job läuft bereits eine Analyse (Web-Lauf oder Sidecar)."""


def _job_lock(owner_id, job, data_root):
    """Prozessübergreifende Sperre je Job — App und Sidecar teilen sich DATA_DIR.

    Ohne sie könnte ein manueller Lauf mit dem Sidecar kollidieren und denselben
    Tag doppelt schreiben. Nicht-blockierend: der zweite Aufrufer scheitert
    sofort und meldet „läuft bereits", statt sich anzustellen.
    """
    slug = job.get("slug") or job.get("id")
    if not slug:
        return None
    directory = store.job_root(owner_id, slug, root=data_root)
    directory.mkdir(parents=True, exist_ok=True)
    handle = open(directory / ".run.lock", "a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise JobBusyError("Für diesen Job läuft bereits eine Analyse.") from None
    return handle


SOURCE_PLACEHOLDERS = ("{sources}", "{journal}")


def build_prompt(template_body, day, summary, state, sources_text, job_name="",
                 known_reminders=()):
    """Prompt aus Vorlage und Kontext bauen — ohne ``str.format``-Fallstricke.

    Alles, was die Vorlage nicht selbst platziert, wird unten angehaengt: Tag,
    Zusammenfassung, Zustand und Quellen. Sonst saehe ein Modell bei einer
    freien Anweisung ohne Platzhalter den Journaltag nie — es meldete dann
    faelschlich „keine Eintraege vorhanden". ``{journal}`` ist ein Alias fuer
    den Quellenblock. Kalender und die schon fest erkannten Erinnerungen
    (``known_reminders``) stehen immer am Ende — sie sind Nachschlagewerk,
    keine Anweisung der Vorlage.
    """
    template = template_body or DEFAULT_TEMPLATE
    rendered = {
        "day": day,
        "summary": summary or "(noch keine)",
        "state": json.dumps(state or {}, ensure_ascii=False, indent=2),
        "sources": sources_text or "(keine Inhalte)",
        "job": job_name or "",
    }
    text = template
    for key, value in rendered.items():
        text = text.replace("{" + key + "}", value)
    if "{journal}" in template:
        text = text.replace("{journal}", rendered["sources"])
    appended = []
    if "{day}" not in template:
        appended.append(f"## Tag\n{rendered['day']}")
    if "{summary}" not in template:
        appended.append(f"## Bisherige Zusammenfassung\n{rendered['summary']}")
    if "{state}" not in template:
        appended.append(f"## Bisheriger strukturierter Zustand\n{rendered['state']}")
    if not any(token in template for token in SOURCE_PLACEHOLDERS):
        appended.append(f"## Quellen des Tages\n{rendered['sources']}")
    try:
        appended.append(jr.calendar_block(date.fromisoformat(str(day))))
    except ValueError:
        pass
    if known_reminders:
        appended.append("## Bereits angelegte Erinnerungen\n" + "\n".join(
            f"- {entry['at'].strftime('%Y-%m-%dT%H:%M')} – {entry['text']}"
            for entry in known_reminders))
    if appended:
        text = text.rstrip() + "\n\n" + "\n\n".join(appended)
    return DEFAULT_SYSTEM_PROMPT, text


def _normalize_reminders(payload):
    """``reminders``-Liste der Modellantwort auf brauchbare Ereignisse reduzieren.

    Jedes Ereignis braucht Text und einen absolut auflösbaren Zeitpunkt; alles
    andere (fehlende Zeit, Unsinn) wird verworfen, statt den Lauf zu kippen.
    """
    raw = payload.get("reminders") if isinstance(payload, dict) else None
    if not isinstance(raw, list):
        return []
    events = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        text = str(entry.get("text") or entry.get("title") or entry.get("message") or "").strip()
        at = ja.parse_notify_at(entry.get("at") or entry.get("notify_at") or entry.get("date"))
        if text and at is not None:
            events.append({"text": text[:ja.MAX_NOTIFICATION_BODY], "at": at})
    return events


def _reminder_key(entry):
    if isinstance(entry, dict):
        entry = entry.get("text") or entry.get("title") or entry.get("message") or ""
    return " ".join(str(entry or "").lower().split())


def new_state_reminders(previous_state, new_state, day=None):
    """Rueckfall: Erinnerungen, die das Modell nur in ``state.reminders`` schrieb.

    Das Antwortformat kennt ``reminders`` zweimal - oben als Benachrichtigung,
    in ``state`` als Gedaechtnis. Lokale Modelle fuellen manchmal nur das
    Gedaechtnis; dann entstand keine terminierte Meldung, sondern sofort die
    Lauf-Zusammenfassung. Uebernommen werden nur Eintraege mit Zeitpunkt, die
    im vorherigen Zustand noch nicht standen - sonst kaeme jede fortgeschriebene
    Erinnerung an jedem Folgetag erneut.

    Das Gedaechtnis besteht in PROD oft aus reinen Saetzen („Morgen 10 Uhr X
    anrufen“) statt Objekten. Mit ``day`` (dem Journaltag) werden sie ueber das
    feste Regelwerk aufgeloest; ohne erkennbaren Termin fallen sie weg.
    """
    raw = new_state.get("reminders") if isinstance(new_state, dict) else None
    if not isinstance(raw, list):
        return []
    previous = previous_state.get("reminders") if isinstance(previous_state, dict) else None
    known = {_reminder_key(item) for item in previous or [] if _reminder_key(item)}
    fresh = [item for item in raw if isinstance(item, (dict, str)) and _reminder_key(item)
             and _reminder_key(item) not in known]
    events = _normalize_reminders({"reminders": [item for item in fresh if isinstance(item, dict)]})
    if day is None:
        return events
    timed = {_reminder_key(entry["text"]) for entry in events}
    for item in fresh:
        sentence = item if isinstance(item, str) else str(
            item.get("text") or item.get("title") or item.get("message") or "")
        if _reminder_key(sentence) in timed:
            continue
        parsed = jr.parse_moment(sentence, day)
        if parsed is None or parsed[0].date() < day:
            continue
        events.append({"text": jr.clean_text(sentence, parsed[1])[:ja.MAX_NOTIFICATION_BODY],
                       "at": parsed[0].replace(tzinfo=ja._local_timezone())})
    return events


# Mehr behält das Gedächtnis nicht: eine endlos wachsende Liste alter
# Erinnerungen verleitet das Modell, Neues als „schon bekannt“ zu verwerfen
# (PROD Oktober 2026: acht Tage lang dieselben 14 Einträge).
MAX_STATE_REMINDERS = 20


def prune_state_reminders(state, day):
    """Abgelaufene Erinnerungen aus ``state.reminders`` streichen, bevor das Modell ihn sieht.

    Abgelaufen ist, was vor dem Journaltag liegt. Bei Saetzen zaehlt nur ein
    absolutes Datum — „morgen“ in einem alten Satz laesst sich nicht mehr
    zuordnen und bleibt stehen, bis die Obergrenze ihn verdraengt.
    """
    if not isinstance(state, dict) or not isinstance(state.get("reminders"), list):
        return state
    start = datetime.combine(day, datetime.min.time()).replace(tzinfo=ja._local_timezone())
    kept = []
    for item in state["reminders"]:
        moment = None
        if isinstance(item, dict):
            moment = ja.parse_notify_at(item.get("at") or item.get("notify_at") or item.get("date"))
            sentence = item.get("text") or item.get("title") or ""
        else:
            sentence = str(item or "")
        if moment is None:
            parsed = jr.parse_moment(sentence, day, relative=False)
            if parsed is not None:
                moment = parsed[0].replace(tzinfo=ja._local_timezone())
        if moment is not None and moment < start:
            continue
        kept.append(item)
    return {**state, "reminders": kept[-MAX_STATE_REMINDERS:]}


def parse_response(text):
    """Antwort in ``(result, summary, state, reminders)`` zerlegen.

    ``state`` ist ``None``, wenn die Antwort keinen brauchbaren Zustand
    enthält — dann bleibt der bisherige erhalten. ``reminders`` ist eine Liste
    benachrichtigungspflichtiger Ereignisse mit gültigem Zeitpunkt;
    Ereignisse ohne auflösbare Zeit werden verworfen.
    """
    raw = str(text or "").strip()
    if not raw:
        return "", "", None, []
    candidate = _FENCE_RE.sub("", raw).strip()
    payload = None
    if candidate.startswith("{"):
        try:
            payload = json.loads(candidate)
        except (ValueError, TypeError):
            payload = None
    if not isinstance(payload, dict):
        # Manchmal steht Text vor oder nach dem JSON-Objekt.
        start, end = candidate.find("{"), candidate.rfind("}")
        if 0 <= start < end:
            try:
                payload = json.loads(candidate[start:end + 1])
            except (ValueError, TypeError):
                payload = None
    if not isinstance(payload, dict):
        return raw[:MAX_RESPONSE_CHARS], raw[:MAX_RESPONSE_CHARS], None, []
    result = str(payload.get("result") or payload.get("analysis") or raw)[:MAX_RESPONSE_CHARS]
    summary = str(payload.get("summary") or result)[:MAX_RESPONSE_CHARS]
    state = payload.get("state")
    if not isinstance(state, dict):
        state = None
    return result, summary, state, _normalize_reminders(payload)


def run_job(owner_id, job, *, today, call_ai, provider, data_root, engine=None,
            prompt_body=None, now=None, is_cancelled=None, force=False):
    """Wie ``_run_job_locked``, aber mit prozessübergreifender Sperre je Job."""
    lock = _job_lock(owner_id, job, data_root)
    try:
        return _run_job_locked(
            owner_id, job, today=today, call_ai=call_ai, provider=provider,
            data_root=data_root, engine=engine, prompt_body=prompt_body, now=now,
            is_cancelled=is_cancelled, force=force,
        )
    finally:
        if lock is not None:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            finally:
                lock.close()


def _run_job_locked(owner_id, job, *, today, call_ai, provider, data_root, engine=None,
                    prompt_body=None, now=None, is_cancelled=None, force=False):
    """Einen Job über seinen rollierenden Zeitraum Tag für Tag ausführen.

    Bereits abgeschlossene, nicht veraltete Tage werden übersprungen; ihr
    ``summary``/``state`` wird aber übernommen, damit die Kette für den nächsten
    offenen Tag stimmt. Hat sich der Nutzertext eines erledigten Tages seither
    geändert (``content_changed``), läuft er erneut. Tage ohne Inhalt werden
    ohne Modellaufruf abgeschlossen. Ein Fehler bricht den Lauf ab — der Job wird nicht
    weiter über einen gescheiterten Tag hinweg fortgeschrieben.
    """
    today = dates.parse_day(today, "Heute")
    try:
        start, end = dates.resolve_range(
            job.get("date_range_mode"),
            today=today,
            custom_start=job.get("custom_start"),
            custom_end=job.get("custom_end"),
            range_days=job.get("range_days"),
        )
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    days = dates.days_in_range(start, end)
    report = {"job_id": job.get("id"), "job": job.get("name", ""), "processed": 0,
              "skipped": 0, "empty": 0, "failed": 0, "days": [], "status": "ok"}
    if is_cancelled and is_cancelled():
        report["status"] = "cancelled"
        return report

    # Das im Job gewaehlte Modell gewinnt ueber das des Anbieters. Sonst wirkte
    # die Auswahl im Formular nicht und ein Anbieter ohne Modell liefe mit dem
    # zufaelligen ersten Fund der Live-Liste.
    job_model = str(job.get("model") or "").strip()
    if job_model and str(provider.get("model") or "").strip() != job_model:
        provider = {**provider, "model": job_model}

    existing = {run["run_date"]: run
                for run in runs.list_runs(owner_id, job_id=job["id"], limit=1000, engine=engine)}

    summary = job.get("summary") or ""
    if job.get("carry_summary"):
        # Uebergabe aus dem letzten Lauf: ueberschrieben und begrenzt, waechst
        # also nicht unbegrenzt an.
        summary = job.get("handoff_summary") or summary
    state = job.get("state") or {}
    last_day = None
    last_run = None
    last_result = ""
    day_results = []
    events = []
    for day in days:
        if is_cancelled and is_cancelled():
            report["status"] = "cancelled"
            break
        day_string = day.isoformat()
        prior = existing.get(day_string)
        collected = sources_module.collect_sources(
            owner_id, day, job.get("extra_sources") or [], data_root)
        changed = bool(prior) and sources_module.content_changed(prior.get("source_hash"), collected)
        if prior and prior.get("status") == "completed" and not prior.get("stale") \
                and not force and not changed:
            report["skipped"] += 1
            report["days"].append({"day": day_string, "status": "skipped"})
            summary = prior.get("summary") or summary
            prior_state = prior.get("state")
            if isinstance(prior_state, dict):
                state = prior_state
            continue
        if sources_module.is_empty(collected):
            # Nichts geschrieben: kein Modellaufruf. Der Tag gilt trotzdem als
            # erledigt (mit Hash), damit er erst wieder drankommt, wenn doch
            # noch etwas eingetragen wird. Zusammenfassung und Zustand laufen
            # unverändert weiter.
            runs.complete_run(
                owner_id, job["id"], day, result=EMPTY_DAY_RESULT, summary=summary, state=state,
                source_hash=sources_module.content_hash(collected), engine=engine,
                data_root=data_root, now=now, notify=False,
            )
            report["empty"] += 1
            report["days"].append({"day": day_string, "status": "empty"})
            continue
        state = prune_state_reminders(state, day)
        found = _journal_reminders(collected, day)
        prompt_system, prompt_user = build_prompt(
            prompt_body, day_string, summary, state,
            sources_module.render_sources(collected), job.get("name", ""), found)
        max_tokens = provider.get("max_tokens")
        if not isinstance(max_tokens, int) or max_tokens <= 0:
            max_tokens = DEFAULT_MAX_TOKENS
        try:
            response = call_ai(
                provider,
                # require_content: ein Reasoning-Modell, das nur denkt und keine
                # Antwort gibt, darf seinen Denktext nicht als Analyse ablegen
                # (PROD 01.10.2026: 882 s, 0 Zeichen Antwort).
                _ai_call_options(system_prompt=prompt_system, max_tokens=max_tokens,
                                 require_content=True),
                prompt_user,
            )
        except Exception as exc:  # noqa: BLE001 - jeder Anbieterfehler zählt als Lauf-Fehler
            runs.fail_run(owner_id, job["id"], day, error_code=exc.__class__.__name__,
                          error_message=str(exc), engine=engine, data_root=data_root, now=now)
            report["failed"] += 1
            report["days"].append({"day": day_string, "status": "failed", "error": str(exc)})
            report["status"] = "failed"
            return report
        result, new_summary, new_state, reminders = parse_response(response)
        if not reminders:
            reminders = new_state_reminders(state, new_state, day)
        reminders = _without_rule_duplicates(reminders, found)
        if new_summary:
            summary = new_summary
        if new_state is not None:
            state = new_state
        run = runs.complete_run(
            owner_id, job["id"], day, result=result, summary=summary, state=state,
            source_hash=sources_module.content_hash(collected), engine=engine,
            data_root=data_root, now=now, notify=False,
        )
        if reminders:
            events.extend({"day": day_string, "run_id": (run or {}).get("id"),
                           "text": entry["text"], "at": entry["at"]}
                          for entry in reminders)
        if found and job.get("output_notification"):
            create_journal_reminders(owner_id, job, day_string, found,
                                     run_id=(run or {}).get("id"), engine=engine,
                                     data_root=data_root, now=now, report=report)
        last_day, last_run, last_result = day, run, result
        day_results.append((day_string, result))
        _apply_outputs(owner_id, job, day, day_string, result, data_root, report)
        report["processed"] += 1
        report["days"].append({"day": day_string, "status": "completed",
                               **({"reason": "changed"} if changed else {})})
    run_summary_text = _run_summary(
        job, days, day_results, provider, call_ai,
        summary or last_result or f"{report['processed']} Tag(e) analysiert.",
        enabled=bool(job.get("output_run_summary")))
    if report.get("processed"):
        _store_handoff(owner_id, job, run_summary_text, engine, data_root, now, report)
        _write_notes(owner_id, job, days, run_summary_text, data_root, report)
    _notify_run(owner_id, job, days, run_summary_text, last_day, last_run, events, report,
                engine, data_root, now)
    return report


def _store_handoff(owner_id, job, text, engine, data_root, now, report):
    if not job.get("carry_summary") or not (text or "").strip():
        return
    try:
        runs.set_handoff_summary(owner_id, job["id"], text, engine=engine,
                                 data_root=data_root, now=now)
    except Exception as exc:  # noqa: BLE001
        report.setdefault("output_errors", []).append(
            {"day": "", "target": "handoff", "error": str(exc)})


def _write_notes(owner_id, job, days, text, data_root, report):
    if not job.get("output_notes") or not (text or "").strip():
        return
    try:
        output.write_notes_entry(owner_id, job, days, text, data_root=data_root)
    except Exception as exc:  # noqa: BLE001
        report.setdefault("output_errors", []).append(
            {"day": "", "target": "notes", "error": str(exc)})


def _run_summary(job, days, day_results, provider, call_ai, fallback, *, enabled=True):
    """Die Ergebnisse mehrerer Tage zu EINER Zusammenfassung verdichten.

    Genau ein zusätzlicher Modellaufruf am Ende — abschaltbar über
    ``output_run_summary``. Ohne ihn müsste die Meldung die Tagesergebnisse
    auflisten (ausdrücklich nicht gewünscht) oder sich auf die letzte
    Modell-Zusammenfassung verlassen, die frühere Tage verlieren kann.
    """
    if not enabled:
        # Schalter aus: wie bisher die fortgeschriebene Zusammenfassung.
        return fallback or (day_results[-1][1] if day_results else "")
    if len(day_results) <= 1:
        return day_results[-1][1] if day_results else (fallback or "")
    sections = "\n\n".join(f"### {day}\n{(text or '').strip()}"
                            for day, text in day_results if (text or "").strip())
    if not sections:
        return fallback or ""
    start = days[0].isoformat() if days else ""
    end = days[-1].isoformat() if days else ""
    prompt = (f"Zeitraum: {start} bis {end}\n\n"
              f"Ergebnisse der einzelnen Tage:\n\n{sections}\n\n"
              "Fasse diese Ergebnisse jetzt zu einer einzigen Zusammenfassung zusammen.")
    budget = provider.get("max_tokens")
    if not isinstance(budget, int) or budget <= 0:
        budget = DEFAULT_MAX_TOKENS
    # Reasoning-Modelle verbrauchen einen Teil des Budgets fuers Denken;
    # großzuegiger bemessen. ``require_content`` verhindert, dass am Ende der
    # Denktext als Zusammenfassung durchgereicht wird.
    max_tokens = max(budget, 8000)
    try:
        response = call_ai(provider, _ai_call_options(
            system_prompt=SUMMARY_SYSTEM_PROMPT, max_tokens=max_tokens,
            require_content=True), prompt)
    except Exception:  # noqa: BLE001 - die Analyse bleibt gueltig
        return fallback or sections
    result, summary, _state, _reminders = parse_response(response)
    return summary or result or fallback or sections


# Bündelung der Erinnerungen: Ein Lauf darf den Tag nicht mit Einzelmeldungen
# bis in die Nacht zerstückeln. Was innerhalb von BUNDLE_WINDOW nach der
# ersten fälligen Erinnerung kommt, erscheint in derselben Meldung; in der
# Ruhezeit (QUIET_START bis QUIET_END Uhr, Server-Lokalzeit) Fälliges
# erscheint erst morgens. Die ursprüngliche Uhrzeit steht dann im Text.
BUNDLE_WINDOW = timedelta(hours=2)
QUIET_START = 21
QUIET_END = 7


def _outside_quiet_hours(at):
    local = at.astimezone(ja._local_timezone())
    morning = {"hour": QUIET_END, "minute": 0, "second": 0, "microsecond": 0}
    if local.hour >= QUIET_START:
        return (local + timedelta(days=1)).replace(**morning)
    if local.hour < QUIET_END:
        return local.replace(**morning)
    return local


def bundle_reminders(events):
    """Erinnerungen zu Meldungen gruppieren: ``[{"at", "entries"}]``, zeitlich sortiert.

    ``at`` ist der Anzeigezeitpunkt der Gruppe (die früheste, ggf. aus der
    Ruhezeit verschobene Fälligkeit); eine Gruppe reicht höchstens
    ``BUNDLE_WINDOW`` über ihren Beginn hinaus, damit sie nicht endlos wächst.
    """
    shifted = sorted(((_outside_quiet_hours(entry["at"]), entry) for entry in events),
                     key=lambda pair: (pair[0], pair[1]["at"]))
    groups = []
    for at, entry in shifted:
        if groups and at - groups[-1]["at"] <= BUNDLE_WINDOW:
            groups[-1]["entries"].append(entry)
        else:
            groups.append({"at": at, "entries": [entry]})
    return groups


def _bundle_body(group):
    """Eine Erinnerung bleibt Klartext; mehrere oder verschobene werden eine Liste
    mit ihren eigentlichen Uhrzeiten."""
    entries = group["entries"]
    at = group["at"]
    if len(entries) == 1 and entries[0]["at"] == at:
        return entries[0]["text"]
    lines = []
    for entry in entries:
        local = entry["at"].astimezone(at.tzinfo)
        stamp = local.strftime("%H:%M") if local.date() == at.date() \
            else local.strftime("%d.%m. %H:%M")
        lines.append(f"• {stamp} – {entry['text']}")
    return "\n".join(lines)[:ja.MAX_NOTIFICATION_BODY]


def _notify_run(owner_id, job, days, body, last_day, last_run, events, report,
                engine, data_root, now):
    """Benachrichtigung am Ende des Laufs — pro Erinnerungs-Bündel, nicht pro Tag.

    Hat die Analyse benachrichtigungspflichtige Ereignisse mit Zeitpunkt
    geliefert (``reminders``), werden sie zeitlich gebündelt
    (``bundle_reminders``); je Bündel entsteht eine Meldung, die erst ab ihrem
    ``notify_at`` sichtbar ist. Ohne Ereignisse bleibt es beim bisherigen
    Rückfall: genau eine Lauf-Zusammenfassung, sofort sichtbar.
    """
    if not report.get("processed") or not job.get("output_notification"):
        return
    if events:
        created = 0
        name = job.get('name') or 'Journal-Analyse'
        for group in bundle_reminders(_without_repeats(owner_id, events, engine)):
            source_days = sorted({entry["day"] for entry in group["entries"]})
            period = source_days[0] if len(source_days) == 1 \
                else f"{source_days[0]} – {source_days[-1]}"
            latest = max(group["entries"], key=lambda entry: entry["day"])
            try:
                if runs.notify_reminder(
                        owner_id, job, day=latest["day"], run_id=latest.get("run_id"),
                        title=f"{name} · {period}", body=_bundle_body(group),
                        notify_at=group["at"], engine=engine, data_root=data_root, now=now):
                    created += 1
            except Exception as exc:  # noqa: BLE001 - die Analyse bleibt gueltig
                report.setdefault("output_errors", []).append(
                    {"day": latest["day"], "target": "notification", "error": str(exc)})
        report["notified"] = created
        report["reminders"] = [{"day": entry["day"], "text": entry["text"][:200],
                                 "at": entry["at"].isoformat()} for entry in events]
        return
    if last_day is None and days:
        last_day = days[-1]
    start = days[0].isoformat() if days else ""
    end = days[-1].isoformat() if days else ""
    period = end if start == end else f"{start} – {end}"
    title = f"{job.get('name') or 'Journal-Analyse'} · {period}"
    try:
        runs.notify_run(
            owner_id, job, day=last_day, title=title, body=body,
            run_id=(last_run or {}).get("id"), engine=engine, data_root=data_root, now=now,
        )
        report["notified"] = True
    except Exception as exc:  # noqa: BLE001 - die Analyse selbst bleibt gueltig
        report.setdefault("output_errors", []).append(
            {"day": "", "target": "notification", "error": str(exc)})


# ─── Feste Journal-Erinnerungen (ohne KI) ─────────────────────────
# "Erinnere mich morgen 10 Uhr …" erkennt journal_reminders.py selbst. Die
# Meldung entsteht spaetestens im naechsten Sidecar-Takt nach dem Schreiben
# (``scan_journal_reminders``) und nicht erst mit der Analyse am Folgetag.
# Sie haengt an einem Job, weil Meldungen in der DB einen brauchen, gilt aber
# je Nutzer nur einmal (``any_job``).
JOURNAL_REMINDER_TITLE = "Journal-Erinnerung"
# Wer "heute 15 Uhr" um 15:02 schreibt, soll die Meldung noch bekommen; eine
# Erinnerung von gestern Mittag beim ersten Scan nach einem Update nicht mehr.
JOURNAL_REMINDER_GRACE = timedelta(hours=1)
_BULLET_PREFIX = re.compile(r"^•\s*(?:\d{2}\.\d{2}\.\s+)?\d{2}:\d{2}\s+–\s+")


def _journal_reminders(collected, day):
    """Feste Erinnerungen aus dem Nutzertext des Tagesjournals, mit lokaler Zeitzone."""
    zone = ja._local_timezone()
    text = sources_module.user_content(collected.get("journal"))
    return [{"text": entry["text"], "at": entry["at"].replace(tzinfo=zone)}
            for entry in jr.find_reminders(text, day)]


def _without_rule_duplicates(reminders, found):
    """KI-Erinnerungen weglassen, die eine feste Erinnerung desselben Tages nur nachsprechen."""
    if not found:
        return reminders
    return [entry for entry in reminders if not any(
        (entry["at"] == known["at"] and jr.related(entry["text"], known["text"]))
        or (entry["at"].date() == known["at"].date() and jr.similar(entry["text"], known["text"]))
        for known in found)]


def _local_day(moment):
    return moment.astimezone(ja._local_timezone()).date()


def _without_repeats(owner_id, events, engine):
    """Dieselbe Erinnerung nur einmal: im Lauf und gegen schon angelegte Meldungen.

    Das Modell wiederholt Erinnerungen aus dem Zustand an Folgetagen (PROD:
    „20.10. 09:00“ aus drei verschiedenen Journaltagen). Gleich ist, was am
    selben Tag faellig wird und fast denselben Text hat. Die Pruefung gegen die
    DB ist Komfort — schlaegt sie fehl, wird wie bisher gemeldet.
    """
    kept = []
    for entry in events:
        if not any(_local_day(entry["at"]) == _local_day(other["at"])
                   and jr.similar(entry["text"], other["text"]) for other in kept):
            kept.append(entry)
    if not kept:
        return kept
    due = [_outside_quiet_hours(entry["at"]) for entry in kept]
    try:
        rows = runs.recent_reminders(owner_id, start=min(due) - timedelta(days=1),
                                     end=max(due) + timedelta(days=1), engine=engine)
    except Exception:  # noqa: BLE001
        return kept
    stored = []
    for row in rows:
        moment = row.get("notify_at")
        if isinstance(moment, str):
            moment = ja.parse_notify_at(moment)
        if moment is None:
            continue
        for line in str(row.get("body") or "").splitlines() or [""]:
            stored.append((_local_day(moment), _BULLET_PREFIX.sub("", line.strip())))
    return [entry for entry, at in zip(kept, due) if not any(
        day == _local_day(at) and jr.similar(entry["text"], text) for day, text in stored)]


def create_journal_reminders(owner_id, job, day_string, found, *, run_id=None, engine=None,
                             data_root=None, now=None, report=None):
    """Feste Erinnerungen als geplante Meldungen anlegen; Rueckgabe: Anzahl neuer.

    Keine Ruhezeit-Verschiebung wie bei KI-Erinnerungen: Die Uhrzeit hat der
    Nutzer ausdruecklich genannt. Pushover haelt seine eigene Ruhezeit ein.
    """
    current = ja._now(now)
    created = 0
    for entry in found:
        if entry["at"] < current - JOURNAL_REMINDER_GRACE:
            continue
        try:
            if runs.notify_reminder(
                    owner_id, job, day=day_string, run_id=run_id,
                    title=f"{JOURNAL_REMINDER_TITLE} · {day_string}", body=entry["text"],
                    notify_at=entry["at"], engine=engine, data_root=data_root, now=now,
                    any_job=True):
                created += 1
        except Exception as exc:  # noqa: BLE001 - eine Meldung darf den Rest nicht kippen
            if report is None:
                raise
            report.setdefault("output_errors", []).append(
                {"day": day_string, "target": "journal_reminder", "error": str(exc)})
    if report is not None and created:
        report["journal_reminders"] = report.get("journal_reminders", 0) + created
    return created


def _reminder_job(jobs):
    """Der Job, an dem die festen Erinnerungen eines Nutzers haengen: bevorzugt der Vortag."""
    candidates = [job for job in jobs if job.get("output_notification")]
    if not candidates:
        return None
    return sorted(candidates, key=lambda job: (job.get("date_range_mode") != "previous_day",
                                               str(job.get("created_at") or "")))[0]


def scan_journal_reminders(*, now, data_root, engine=None, days=2):
    """Heutiges und gestriges Journal aller Nutzer nach festen Erinnerungen absuchen.

    Laeuft in jedem Sidecar-Takt. Nur Nutzer mit einem aktiven Job, der
    Benachrichtigungen erzeugt — wer die Analyse nicht nutzt, bekommt auch
    keine Erinnerungen ungefragt. Gestern zaehlt mit, weil nach Mitternacht
    oft noch in den Vortag geschrieben wird.
    """
    by_owner = {}
    for job in ja.list_enabled_jobs(engine=engine):
        by_owner.setdefault(job["owner_id"], []).append(job)
    today = now.date()
    created = 0
    for owner_id, jobs in by_owner.items():
        job = _reminder_job(jobs)
        if job is None:
            continue
        for offset in range(days - 1, -1, -1):
            day = today - timedelta(days=offset)
            found = _journal_reminders(sources_module.collect_sources(owner_id, day, [], data_root), day)
            if found:
                created += create_journal_reminders(owner_id, job, day.isoformat(), found,
                                                    engine=engine, data_root=data_root, now=now)
    return created


def _apply_outputs(owner_id, job, day, day_string, result, data_root, report):
    """Ausgabemodi anwenden (JA-08). Ein Ausgabefehler kippt den Lauf nicht.

    Die Analyse ist bereits gelungen und steht in DB und Benachrichtigung; ein
    fehlgeschlagenes Schreiben ins Journal oder Familiendokument wird gesammelt
    gemeldet, statt den erfolgreichen Lauf nachträglich als Fehler zu werten.
    """
    if not (result or "").strip():
        return
    if job.get("output_journal"):
        try:
            output.write_journal_entry(owner_id, day, job, result, data_root=data_root)
        except Exception as exc:  # noqa: BLE001
            report.setdefault("output_errors", []).append(
                {"day": day_string, "target": "journal", "error": str(exc)})
    if job.get("output_family") and job.get("family_target"):
        try:
            output.write_family_note(job["family_target"], job, day, result, data_root=data_root)
        except Exception as exc:  # noqa: BLE001
            report.setdefault("output_errors", []).append(
                {"day": day_string, "target": "family", "error": str(exc)})


def latest_run_at(owner_id, job_id, *, engine=None):
    """Zeitpunkt des letzten Laufs (für die Fälligkeitsprüfung)."""
    items = runs.list_runs(owner_id, job_id=job_id, limit=1, engine=engine)
    if not items:
        return None
    value = items[0].get("started_at") or items[0].get("created_at")
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


__all__ = [
    "DEFAULT_SYSTEM_PROMPT", "DEFAULT_TEMPLATE", "build_prompt", "parse_response",
    "run_job", "JobBusyError", "SOURCE_PLACEHOLDERS", "latest_run_at",
    "new_state_reminders", "prune_state_reminders", "create_journal_reminders",
    "scan_journal_reminders",
]