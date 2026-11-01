"""Kaufhistorie je Einkaufsliste: was wurde wann abgehakt.

Grundlage fuer „Klassiker ergaenzen“ im Einkaufslisten-Agenten. Abgehakte
Artikel verschwanden bisher spurlos, sobald jemand die Liste aufraeumte; nur
was sieben Tage abgehakt liegen blieb, landete im Familien-Archiv. Deshalb
schreiben „Liste aufraeumen“ und das Entfernen durch den Agenten die
abgehakten Artikel vorher hierher.

Gespeichert unter ``family/shopping-history/<projekt-id>.json`` als
Ereignisliste ``{id, title, gruppe, date}``. Ein Artikel zaehlt einmal (seine
Aufgaben-ID), egal wie oft er an- und abgehakt wurde. Ausgewertet werden
zusaetzlich das Familien-Archiv und die gerade abgehakten Artikel der Liste —
so gibt es vom ersten Tag an eine Historie, ohne Migration.

JSON statt Markdown mit Absicht: Brain indiziert den Familienordner, und eine
Liste mit tausend Einkaufsereignissen gehoert nicht in die Suche.
"""

import json
import re
from datetime import date, datetime

import family as family_module
from scheduling import read_text_file, update_text_file


HISTORY_DIRNAME = "shopping-history"
MAX_EVENTS = 3000
DEFAULT_LIMIT = 40
# „Faellig“: seit dem letzten Kauf ist fast ein ueblicher Abstand vergangen.
DUE_FACTOR = 0.8
_QUANTITY = re.compile(
    r"^\s*(?:\d+(?:[.,]\d+)?\s*(?:x|stk\.?|stück|stueck|st\.|g|kg|l|ml|pck\.?|packungen?|packs?|"
    r"flaschen?|dosen?|becher|beutel|bund)?\s+)", re.IGNORECASE)


def _path(project_id):
    return family_module.FAMILY_DIR / HISTORY_DIRNAME / f"{project_id}.json"


def normalize(title):
    """Vergleichsschluessel: ohne Mengenangabe vorn, klein, ohne Satzzeichen."""
    text = _QUANTITY.sub("", str(title or ""))
    return " ".join(re.sub(r"[^\w\s]", " ", text.lower()).split())


def _event_date(task):
    value = task.get("completed_at") or task.get("created_at") or ""
    try:
        return datetime.fromisoformat(str(value)).date().isoformat()
    except ValueError:
        return ""


def _event(task):
    title = str(task.get("title") or "").strip()
    day = _event_date(task)
    if not title or not day or not task.get("id"):
        return None
    return {"id": str(task["id"]), "title": title, "gruppe": str(task.get("gruppe") or ""), "date": day}


def _load(project_id):
    raw = read_text_file(_path(project_id)) if _path(project_id).exists() else ""
    try:
        data = json.loads(raw or "{}")
    except ValueError:
        return []
    events = data.get("events") if isinstance(data, dict) else None
    return [item for item in events or [] if isinstance(item, dict)]


def record(project_id, tasks):
    """Abgehakte Artikel festhalten, bevor sie von der Liste verschwinden.

    Wiederkehrende Aufgaben gehoeren dem Planer und zaehlen nicht. Fehler beim
    Schreiben sind kein Grund, das Aufraeumen scheitern zu lassen — der
    Aufrufer faengt sie ab.
    """
    if not family_module._valid_id(project_id):
        return 0
    fresh = [event for event in (
        _event(task) for task in tasks or []
        if task.get("completed") and task.get("source") != "recurring" and not task.get("recurring"))
        if event]
    if not fresh:
        return 0
    path = _path(project_id)
    path.parent.mkdir(parents=True, exist_ok=True)

    def update(current):
        try:
            data = json.loads(current or "{}")
        except ValueError:
            data = {}
        events = [item for item in (data.get("events") or []) if isinstance(item, dict)]
        known = {item.get("id") for item in events}
        added = [event for event in fresh if event["id"] not in known]
        if not added:
            return current, 0
        events = (events + added)[-MAX_EVENTS:]
        return json.dumps({"schema": 1, "events": events}, ensure_ascii=False, indent=1), len(added)

    return update_text_file(path, update) or 0


def _archive_events(project_id):
    archive = family_module.ARCHIVE_DIR
    if not archive.is_dir():
        return []
    events = []
    for path in archive.glob("*.md"):
        item = family_module._parse_archive_file(path)
        if not item or item.get("project_id") != project_id:
            continue
        event = _event({"id": item.get("id"), "title": item.get("title"),
                        "completed_at": item.get("completed_at"), "completed": True})
        if event:
            events.append(event)
    return events


def events(project_id, project=None):
    """Alle bekannten Kaeufe der Liste: Historie, Archiv und aktuelle Haken."""
    current = [_event(task) for task in (project or {}).get("tasks", [])
               if task.get("completed") and task.get("source") != "recurring"
               and not task.get("recurring")]
    merged, seen = [], set()
    for event in _load(project_id) + _archive_events(project_id) + [item for item in current if item]:
        if event.get("id") in seen:
            continue
        seen.add(event.get("id"))
        merged.append(event)
    return merged


def summary(project_id, project=None, *, today=None, limit=DEFAULT_LIMIT):
    """Je Artikel: wie oft, zuletzt wann, ueblicher Abstand, faellig, schon auf der Liste.

    Sortiert nach: faellig zuerst, dann haeufig, dann kuerzlich. Ein einmal
    gekaufter Artikel ist kein Klassiker und gilt nie als faellig.
    """
    today = today or date.today()
    on_list = {normalize(task.get("title")) for task in (project or {}).get("tasks", [])
               if not task.get("completed")}
    groups = {}
    for event in events(project_id, project):
        key = normalize(event["title"])
        if not key:
            continue
        try:
            day = date.fromisoformat(event["date"])
        except ValueError:
            continue
        entry = groups.setdefault(key, {"days": set(), "title": event["title"], "gruppe": "",
                                        "last": day})
        entry["days"].add(day)
        if day >= entry["last"]:
            entry["last"], entry["title"] = day, event["title"]
        if event.get("gruppe") and event.get("gruppe") != "Unsortiert":
            entry["gruppe"] = event["gruppe"]
    items = []
    for key, entry in groups.items():
        days = sorted(entry["days"])
        count = len(days)
        rhythm = round((days[-1] - days[0]).days / (count - 1)) if count >= 2 else None
        since = (today - entry["last"]).days
        due = bool(rhythm) and since >= rhythm * DUE_FACTOR
        items.append({
            "titel": entry["title"], "rubrik": entry["gruppe"], "gekauft": count,
            "zuletzt": entry["last"].isoformat(), "tage_seit": since,
            "rhythmus_tage": rhythm, "faellig": due, "auf_der_liste": key in on_list,
        })
    items.sort(key=lambda item: (not item["faellig"], -item["gekauft"], item["tage_seit"]))
    return {"artikel": items[:max(1, limit)], "artikel_gesamt": len(items),
            "kaeufe_gesamt": sum(item["gekauft"] for item in items)}


__all__ = ["HISTORY_DIRNAME", "MAX_EVENTS", "normalize", "record", "events", "summary"]
