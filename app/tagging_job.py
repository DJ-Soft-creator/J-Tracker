"""Der Job „Hashtags nachtragen“: Zeitplan, Zustand und Meldungen.

AI maintenance contract: dieses Modul kennt weder Flask noch das Modell. Es
normalisiert die Job-Felder, die neben der Verbindung in
``.config/historical-tagging.json`` stehen, entscheidet ueber die Faelligkeit
(dieselben Regeln wie die Journal-Analyse, ``journal_analysis_dates``), haelt den
Zustand des Zeitplans und sammelt die Meldungen, die der Pushover-Versand im
Analyse-Sidecar abholt. Die Klassifikation selbst bleibt in
``historical_tagging.py``.

Warum Datei statt DB: Der Job ist pro Nutzer genau einer, und er muss auch ohne
Postgres laufen — die Verschlagwortung war nie an die Datenbank gebunden.
"""

import json
import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import journal_analysis_dates as dates
from scheduling import read_text_file, update_text_file


STATE_NAME = "historical-tagging-state.json"
LEGACY_DAILY_STATE_NAME = "historical-tagging-daily.json"
EVENTS_NAME = "historical-tagging-events.json"

SCHEDULE_MODES = ("daily", "interval", "custom", "manual")
RUN_MODES = ("missing", "refresh")
NOTIFY_KINDS = ("errors", "results", "proposals")
DEFAULT_NOTIFY = {"errors": True, "results": False, "proposals": False}
DEFAULT_RANGE_DAYS = 7
MAX_RANGE_DAYS = 62
DEFAULT_TIME = "05:00"
# Ein toter Endpunkt um fuenf Uhr frueh ist oft nur ein schlafender Rechner.
# Deshalb nicht sofort aufgeben, aber auch nicht alle fuenf Minuten erneut.
MAX_ATTEMPTS = 3
RETRY_MINUTES = 30
MAX_EVENTS = 50

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

DEFAULTS = {
    "enabled": False,
    "schedule_mode": "daily",
    "schedule_time": DEFAULT_TIME,
    "schedule_interval_hours": None,
    "schedule_rule": None,
    "range_days": DEFAULT_RANGE_DAYS,
    "mode": "missing",
    "notify": dict(DEFAULT_NOTIFY),
}


# ─── Job-Felder ───────────────────────────────────────────────────────
def _clean_rule(value):
    if not isinstance(value, dict):
        return None
    kind = value.get("kind")
    if kind == "weekly":
        key, low, high = "weekdays", 0, 6
    elif kind == "monthly":
        key, low, high = "days", 1, 31
    else:
        return None
    items = set()
    for item in value.get(key) or []:
        try:
            number = int(item)
        except (TypeError, ValueError):
            return None
        if not low <= number <= high:
            return None
        items.add(number)
    return {"kind": kind, key: sorted(items)} if items else None


def _int_or_none(value, low, high):
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if low <= number <= high else None


def stored_job(stored):
    """Die Job-Felder aus der Datei — tolerant, weil sie auch von Hand krumm sein kann.

    Aeltere Dateien kennen nur ``daily`` und ``daily_hour``; daraus wird ein
    taeglicher Job zur vollen Stunde, damit ein bestehender Tageslauf nach dem
    Update weiterlaeuft, statt still zu verschwinden.
    """
    stored = stored if isinstance(stored, dict) else {}
    job = {key: (dict(value) if isinstance(value, dict) else value) for key, value in DEFAULTS.items()}
    if "enabled" in stored:
        job["enabled"] = bool(stored.get("enabled"))
    elif "daily" in stored:
        job["enabled"] = bool(stored.get("daily"))
    if stored.get("schedule_mode") in SCHEDULE_MODES:
        job["schedule_mode"] = stored["schedule_mode"]
    time_value = str(stored.get("schedule_time") or "")
    if _TIME_RE.match(time_value):
        job["schedule_time"] = time_value
    elif "schedule_time" not in stored:
        hour = _int_or_none(stored.get("daily_hour"), 0, 23)
        if hour is not None:
            job["schedule_time"] = "{:02d}:00".format(hour)
    job["schedule_interval_hours"] = _int_or_none(stored.get("schedule_interval_hours"), 1, 720)
    job["schedule_rule"] = _clean_rule(stored.get("schedule_rule"))
    range_days = _int_or_none(stored.get("range_days"), 1, MAX_RANGE_DAYS)
    if range_days is not None:
        job["range_days"] = range_days
    if stored.get("mode") in RUN_MODES:
        job["mode"] = stored["mode"]
    notify = stored.get("notify") if isinstance(stored.get("notify"), dict) else {}
    job["notify"] = {kind: bool(notify.get(kind, DEFAULT_NOTIFY[kind])) for kind in NOTIFY_KINDS}
    return job


def clean_job(raw, current):
    """Eine Teilaenderung der Job-Felder pruefen; fehlende Felder bleiben, wie sie sind.

    ``daily``/``daily_hour`` werden weiter angenommen: aeltere Oberflaechen und
    Kurzbefehle schicken nur diese beiden.
    """
    raw = raw if isinstance(raw, dict) else {}
    job = stored_job(current)
    if "enabled" in raw:
        job["enabled"] = bool(raw.get("enabled"))
    elif "daily" in raw:
        job["enabled"] = bool(raw.get("daily"))
    if "schedule_mode" in raw:
        if raw.get("schedule_mode") not in SCHEDULE_MODES:
            raise ValueError("Unbekannter Zeitplan.")
        job["schedule_mode"] = raw["schedule_mode"]
    if "schedule_time" in raw:
        value = str(raw.get("schedule_time") or "").strip()
        if not _TIME_RE.match(value):
            raise ValueError("Die Uhrzeit bitte als HH:MM angeben.")
        job["schedule_time"] = value
    elif "daily_hour" in raw:
        hour = _int_or_none(raw.get("daily_hour"), 0, 23)
        if hour is None:
            raise ValueError("Die Stunde des Tageslaufs muss zwischen 0 und 23 liegen.")
        job["schedule_time"] = "{:02d}:00".format(hour)
    if "schedule_interval_hours" in raw:
        hours = raw.get("schedule_interval_hours")
        job["schedule_interval_hours"] = _int_or_none(hours, 1, 720) if hours not in (None, "") else None
    if "schedule_rule" in raw:
        job["schedule_rule"] = _clean_rule(raw.get("schedule_rule"))
    if "range_days" in raw:
        range_days = _int_or_none(raw.get("range_days"), 1, MAX_RANGE_DAYS)
        if range_days is None:
            raise ValueError("Der Zeitraum muss zwischen 1 und {} Tagen liegen.".format(MAX_RANGE_DAYS))
        job["range_days"] = range_days
    if "mode" in raw:
        if raw.get("mode") not in RUN_MODES:
            raise ValueError("Unbekannter Modus.")
        job["mode"] = raw["mode"]
    if isinstance(raw.get("notify"), dict):
        job["notify"] = {
            kind: bool(raw["notify"].get(kind, job["notify"][kind])) for kind in NOTIFY_KINDS
        }
    if job["schedule_mode"] == "interval" and not job["schedule_interval_hours"]:
        raise ValueError("Für einen Takt die Stunden angeben.")
    if job["schedule_mode"] == "custom" and not job["schedule_rule"]:
        raise ValueError("Für einen benutzerdefinierten Zeitplan Wochentage oder Monatstage angeben.")
    if job["schedule_mode"] != "custom":
        job["schedule_rule"] = None
    return job


def scheduled_range(job, today):
    """``(start, end)`` als ISO-Daten: die letzten ``range_days`` Tage inklusive heute.

    Heute zaehlt mit, weil jeder Eintrag sich merkt, ob das Modell ihn schon
    gesehen hat — was spaeter am Tag dazukommt, holt der naechste Lauf nach.
    """
    start, end = dates.resolve_range("last_days", today=today, range_days=job["range_days"])
    return start.isoformat(), end.isoformat()


# ─── Zustand des Zeitplans ────────────────────────────────────────────
def _config_dir(data_dir, user_id):
    return Path(data_dir) / user_id / ".config"


def _load(path, default):
    path = Path(path)
    if not path.is_file() or path.is_symlink():
        return default
    try:
        value = json.loads(read_text_file(path) or "null")
    except (OSError, ValueError):
        return default
    return value if isinstance(value, type(default)) else default


def _update(path, mutate, default):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    def updater(current):
        try:
            value = json.loads(current) if current else default
        except ValueError:
            value = default
        if not isinstance(value, type(default)):
            value = default
        value = mutate(value)
        return json.dumps(value, ensure_ascii=False, indent=2) + "\n", value

    return update_text_file(path, updater)


def load_state(data_dir, user_id):
    """``last_run_at`` (lokale ISO-Zeit), Fehlversuche und Sperrfrist des Zeitplans.

    Der alte Tagesstempel (nur ein Datum) zaehlt als Lauf um Mitternacht dieses
    Tages: so startet ein schon gelaufener Tag nach dem Update nicht ein zweites Mal.
    """
    directory = _config_dir(data_dir, user_id)
    state = _load(directory / STATE_NAME, {})
    if not state.get("last_run_at"):
        legacy = _load(directory / LEGACY_DAILY_STATE_NAME, {})
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(legacy.get("last_run") or "")):
            state["last_run_at"] = legacy["last_run"] + "T00:00:00"
    state.setdefault("last_run_at", "")
    state.setdefault("attempts", 0)
    state.setdefault("retry_after", "")
    return state


def update_state(data_dir, user_id, mutate):
    def change(state):
        mutate(state)
        return state

    return _update(_config_dir(data_dir, user_id) / STATE_NAME, change, {})


def _parse(value, reference):
    try:
        moment = datetime.fromisoformat(str(value or ""))
    except ValueError:
        return None
    if reference.tzinfo is not None and moment.tzinfo is None:
        return moment.replace(tzinfo=reference.tzinfo)
    if reference.tzinfo is None and moment.tzinfo is not None:
        return moment.astimezone(None).replace(tzinfo=None)
    return moment


def is_due(job, state, now):
    """Ob der Zeitplan jetzt einen Lauf startet. Eine Sperrfrist nach einem
    Fehlversuch geht vor, sonst gelten die Regeln der Journal-Analyse."""
    retry_after = _parse(state.get("retry_after"), now)
    if retry_after and now < retry_after:
        return False
    return dates.is_due(job, now, _parse(state.get("last_run_at"), now))


def record_attempt(data_dir, user_id, now, ok):
    """Nach einem Lauf des Zeitplans: erledigt — oder spaeter erneut versuchen.

    Gibt ``True`` zurueck, wenn der Termin damit abgeschlossen ist (Erfolg oder
    letzter Versuch). Nur dann darf eine Fehlermeldung raus: sonst kaeme bei
    einem kurz schlafenden Endpunkt jede halbe Stunde eine.
    """
    result = {}

    def mutate(state):
        attempts = int(state.get("attempts") or 0) + (0 if ok else 1)
        if ok or attempts >= MAX_ATTEMPTS:
            state.update(last_run_at=now.isoformat(timespec="seconds"), attempts=0, retry_after="")
            result["final"] = True
        else:
            state.update(attempts=attempts,
                         retry_after=(now + timedelta(minutes=RETRY_MINUTES)).isoformat(timespec="seconds"))
            result["final"] = False

    update_state(data_dir, user_id, mutate)
    return result["final"]


# ─── Meldungen fuer Pushover ──────────────────────────────────────────
def _events_path(data_dir, user_id):
    return _config_dir(data_dir, user_id) / EVENTS_NAME


def events_for_run(job, *, status, result=None, error="", trigger="manual"):
    """Welche Meldungen ein beendeter Lauf ausloest — nach den Schaltern des Jobs.

    Eine Vorschau meldet nie etwas: sie hat nichts geschrieben.
    """
    result = result or {}
    if result.get("preview"):
        return []
    notify = job.get("notify") or DEFAULT_NOTIFY
    events = []
    failed = int(result.get("failed") or 0)
    if notify.get("errors") and (status == "failed" or failed):
        events.append({"kind": "tagging_error", "count": max(1, failed)})
    processed = int(result.get("processed") or 0)
    if notify.get("results") and status == "done" and processed:
        events.append({"kind": "tagging_result", "count": processed})
    proposals = len(result.get("proposals") or [])
    if notify.get("proposals") and proposals:
        events.append({"kind": "tagging_proposals", "count": proposals})
    for event in events:
        event["trigger"] = trigger
    return events


def add_events(data_dir, user_id, events, now):
    if not events:
        return []
    stamped = [
        {**event, "id": "tagging-" + str(uuid.uuid4()), "created_at": now.isoformat(timespec="seconds")}
        for event in events
    ]

    def mutate(items):
        return (items + stamped)[-MAX_EVENTS:]

    _update(_events_path(data_dir, user_id), mutate, [])
    return stamped


def list_events(data_dir, user_id):
    """Die juengsten Meldungen, neueste zuletzt. Der Versand merkt sich selbst,
    was schon raus ist (``pushover_state.json``), deshalb wird hier nichts geloescht."""
    return [item for item in _load(_events_path(data_dir, user_id), []) if isinstance(item, dict)]


__all__ = [
    "DEFAULTS", "NOTIFY_KINDS", "RUN_MODES", "SCHEDULE_MODES",
    "stored_job", "clean_job", "scheduled_range", "load_state", "update_state",
    "is_due", "record_attempt", "events_for_run", "add_events", "list_events",
]
