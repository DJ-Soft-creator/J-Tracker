"""Pushover-Benachrichtigungen pro Nutzer.

AI maintenance contract:
Own the per-user Pushover settings (``<DATA_DIR>/<user>/pushover.json``), the
delivery state (``pushover_state.json``), the neutral message texts, quiet
hours and the single HTTP call to Pushover. HTTP routes live in
``pushover_routes.py``; the periodic dispatch runs in
``scripts/journal-analysis-scheduler.py`` via ``dispatch_all``.

Warum dateibasiert statt DB: Einstellungen gehoeren wie Hashtag-Katalog und
Agent-Projekte in den Nutzerordner (atomar ueber ``scheduling``); nur ein
Prozess - der Analyse-Scheduler - versendet, damit gibt es keine Doppelzustellung
zwischen Web und Sidecar. ``users.json`` bleibt unberuehrt (bekannter
Read-Modify-Write-Befund in AI_CONTEXT).

Inhalt bewusst neutral (Anforderung-Pushover-Benachrichtigungen.md): nur Thema
und Anzahl, nie Journal-Text, Titel oder Projektnamen. Versand nur an den festen
Host ``api.pushover.net`` - keine vom Nutzer steuerbare URL (kein SSRF-Kanal).
"""

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, time as dtime
from pathlib import Path

from scheduling import read_text_file, update_text_file


API_URL = "https://api.pushover.net/1/messages.json"
_KEY_RE = re.compile(r"^[A-Za-z0-9]{30}$")
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_SENT_KEEP = 500
SEND_TIMEOUT = 15

TRIGGERS = ("reminders", "results", "errors", "family", "tagging")
# Meldungen des Jobs „Hashtags nachtragen“ (tagging_job.py). Welche Arten
# entstehen, entscheidet der Job selbst; hier gibt es nur den Hauptschalter.
TAGGING_KINDS = ("tagging_error", "tagging_proposals", "tagging_result")
# Analyse-Benachrichtigung (``kind``) -> Schalter im Profil.
KIND_TRIGGER = {"reminder": "reminders", "analysis": "results", "summary": "results", "error": "errors"}

DEFAULTS = {
    "user_key": "",
    "app_token": "",
    "triggers": {name: True for name in TRIGGERS},
    "quiet_start": "22:00",
    "quiet_end": "07:00",
    "family_time": "07:30",
    "base_url": "",
    "enabled_at": "",
}


class PushoverError(ValueError):
    """Ungueltige Einstellung oder fehlgeschlagener Versand."""


# ─── Ablage ───────────────────────────────────────────────────────────
def _settings_path(data_dir, user_id):
    return Path(data_dir) / user_id / "pushover.json"


def _state_path(data_dir, user_id):
    return Path(data_dir) / user_id / "pushover_state.json"


def _load_json(path):
    try:
        raw = read_text_file(path)
    except FileNotFoundError:
        return {}
    try:
        value = json.loads(raw or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _clean(raw):
    raw = raw if isinstance(raw, dict) else {}
    triggers = raw.get("triggers") if isinstance(raw.get("triggers"), dict) else {}
    settings = {
        "user_key": str(raw.get("user_key") or "").strip(),
        "app_token": str(raw.get("app_token") or "").strip(),
        "triggers": {name: triggers.get(name, True) is not False for name in TRIGGERS},
        "base_url": str(raw.get("base_url") or "").strip(),
        "enabled_at": str(raw.get("enabled_at") or "").strip(),
    }
    for field in ("quiet_start", "quiet_end", "family_time"):
        value = str(raw.get(field) if raw.get(field) is not None else DEFAULTS[field]).strip()
        settings[field] = value if (value == "" or _TIME_RE.match(value)) else DEFAULTS[field]
    return settings


def load_settings(data_dir, user_id):
    return _clean(_load_json(_settings_path(data_dir, user_id)))


def load_state(data_dir, user_id):
    state = _load_json(_state_path(data_dir, user_id))
    state.setdefault("sent", [])
    state.setdefault("family_last", "")
    state.setdefault("last_result", None)
    state.setdefault("halted", "")
    return state


def update_state(data_dir, user_id, mutate):
    path = _state_path(data_dir, user_id)
    path.parent.mkdir(parents=True, exist_ok=True)

    def updater(current):
        try:
            state = json.loads(current or "{}")
        except ValueError:
            state = {}
        if not isinstance(state, dict):
            state = {}
        mutate(state)
        state["sent"] = list(state.get("sent") or [])[-_SENT_KEEP:]
        return json.dumps(state, ensure_ascii=False, indent=2) + "\n"

    update_text_file(path, updater)


def configured(settings):
    return bool(settings.get("user_key") and settings.get("app_token"))


def mask(value):
    value = str(value or "")
    return "••••" + value[-3:] if value else ""


def public_view(settings, state):
    """Was die Oberflaeche sehen darf: Zugangsdaten nur maskiert."""
    return {
        "configured": configured(settings),
        "user_key_masked": mask(settings["user_key"]),
        "app_token_masked": mask(settings["app_token"]),
        "triggers": settings["triggers"],
        "quiet_start": settings["quiet_start"],
        "quiet_end": settings["quiet_end"],
        "family_time": settings["family_time"],
        "last_result": state.get("last_result"),
        "halted": state.get("halted") or "",
    }


def save_settings(data_dir, user_id, patch, *, base_url="", now=None):
    """Teilaenderung uebernehmen. Leere Schluessel-Felder behalten den alten
    Wert; ``clear_credentials`` entfernt beide."""
    patch = patch if isinstance(patch, dict) else {}
    path = _settings_path(data_dir, user_id)
    path.parent.mkdir(parents=True, exist_ok=True)

    def updater(current):
        try:
            existing = _clean(json.loads(current or "{}"))
        except ValueError:
            existing = _clean({})
        merged = dict(existing)
        if patch.get("clear_credentials"):
            merged["user_key"] = merged["app_token"] = ""
        for field in ("user_key", "app_token"):
            value = str(patch.get(field) or "").strip()
            if value:
                if not _KEY_RE.match(value):
                    label = "User-Key" if field == "user_key" else "API-Token"
                    raise PushoverError(f"Der {label} hat 30 Zeichen aus Buchstaben und Ziffern.")
                merged[field] = value
        if isinstance(patch.get("triggers"), dict):
            merged["triggers"] = {name: patch["triggers"].get(name, merged["triggers"][name]) is not False
                                  for name in TRIGGERS}
        for field in ("quiet_start", "quiet_end", "family_time"):
            if field in patch:
                value = str(patch.get(field) or "").strip()
                if value and not _TIME_RE.match(value):
                    raise PushoverError("Uhrzeiten bitte als HH:MM angeben.")
                merged[field] = value
        if base_url:
            merged["base_url"] = base_url
        # Ab dem Einrichten zaehlt: aeltere Analyse-Hinweise werden nicht
        # nachtraeglich als Flut verschickt.
        if configured(merged) and not configured(existing):
            merged["enabled_at"] = (now or datetime.now().astimezone()).isoformat(timespec="seconds")
        if not configured(merged):
            merged["enabled_at"] = ""
        return json.dumps(merged, ensure_ascii=False, indent=2) + "\n", merged

    settings = update_text_file(path, updater)
    # Neue Zugangsdaten heben einen frueheren Dauerfehler auf.
    update_state(data_dir, user_id, lambda state: state.update(halted=""))
    return settings


# ─── Zeit ─────────────────────────────────────────────────────────────
def _parse_time(value):
    hours, minutes = value.split(":")
    return dtime(int(hours), int(minutes))


def in_quiet_hours(settings, now):
    start, end = settings.get("quiet_start"), settings.get("quiet_end")
    if not start or not end or start == end:
        return False
    current = now.time().replace(second=0, microsecond=0)
    start_t, end_t = _parse_time(start), _parse_time(end)
    if start_t < end_t:
        return start_t <= current < end_t
    return current >= start_t or current < end_t  # ueber Mitternacht


# ─── Nachrichten ──────────────────────────────────────────────────────
def _plural(count, one, many):
    return one if count == 1 else many.format(n=count)


def _clock_label(moment, today):
    return moment.strftime("%H:%M") if moment.date() == today else moment.strftime("%d.%m. %H:%M")


def _reminder_message(moments, today):
    """Uhrzeit und Journaltag, nie der Erinnerungstext: Er kann Namen und
    Privates enthalten und laege sonst auf dem Sperrbildschirm."""
    labels = [_clock_label(moment, today) for moment, _day in moments]
    if len(moments) == 1:
        day = moments[0][1]
        source = f" (Journal vom {day.strftime('%d.%m.')})" if day else ""
        return f"Erinnerung für {labels[0]}{source}."
    shown = labels[:5] + (["…"] if len(labels) > 5 else [])
    listed = ", ".join(shown[:-1]) + " und " + shown[-1]
    return f"{len(moments)} Erinnerungen für {listed}."


def message_for(trigger, count=1, overdue=0, *, moments=None, now=None):
    """Neutraler Text: nur Thema, Anzahl und bei Erinnerungen die Uhrzeit.

    ``moments``: ``[(faellig_lokal, journaltag|None)]`` je Erinnerung — fehlen
    sie (aeltere Meldungen ohne Zeitpunkt), bleibt es beim reinen Zaehler.
    """
    if trigger == "reminders":
        if moments and len(moments) == count:
            today = (now or moments[0][0]).date()
            return "Journl · Erinnerung", _reminder_message(sorted(moments, key=lambda pair: pair[0]), today), 0
        return ("Journl · Erinnerung",
                _plural(count, "Eine neue Erinnerung aus der Journal-Analyse.",
                        "{n} neue Erinnerungen aus der Journal-Analyse."), 0)
    if trigger == "results":
        return ("Journl · Analyse",
                _plural(count, "Ein neues Analyse-Ergebnis liegt bereit.",
                        "{n} neue Analyse-Ergebnisse liegen bereit."), 0)
    if trigger == "errors":
        return ("Journl · Fehler",
                _plural(count, "Die Journal-Analyse meldet einen Fehler.",
                        "Die Journal-Analyse meldet {n} Fehler."), 1)
    if trigger == "family":
        parts = []
        if count:
            parts.append(_plural(count, "1 Aufgabe fällig", "{n} Aufgaben fällig"))
        if overdue:
            parts.append(f"{overdue} überfällig")
        return "Journl · Familie", "Heute " + ", ".join(parts) + ".", 0
    if trigger == "tagging_error":
        return ("Journl · Hashtags",
                _plural(count, "Hashtags nachtragen meldet einen Fehler.",
                        "Hashtags nachtragen meldet {n} Fehler."), 1)
    if trigger == "tagging_result":
        return ("Journl · Hashtags",
                _plural(count, "Hashtags für einen Tag nachgetragen.",
                        "Hashtags für {n} Tage nachgetragen."), 0)
    if trigger == "tagging_proposals":
        return ("Journl · Hashtags",
                _plural(count, "Ein neuer Hashtag-Vorschlag wartet auf Freigabe.",
                        "{n} neue Hashtag-Vorschläge warten auf Freigabe."), 0)
    raise PushoverError(f"Unbekannter Ausloeser: {trigger}")


def deep_link(settings, trigger):
    base = (settings.get("base_url") or "").rstrip("/")
    if not base:
        return "", ""
    if trigger == "family":
        return f"{base}/?tab=family", "In Journl öffnen"
    if str(trigger).startswith("tagging"):
        return f"{base}/?open=tagging", "Protokoll öffnen"
    return f"{base}/?tab=write&open=notifications", "In Journl öffnen"


# ─── Versand ──────────────────────────────────────────────────────────
def send(settings, title, message, *, priority=0, url="", url_title="", opener=None):
    """Eine Nachricht an Pushover. Gibt ``(ok, fehlertext, dauerhaft)`` zurueck.

    ``dauerhaft`` heisst: Pushover lehnt die Zugangsdaten ab (4xx ausser 429) -
    weitere Versuche sind bis zur naechsten Aenderung sinnlos.
    """
    if not configured(settings):
        return False, "Pushover ist nicht eingerichtet.", True
    fields = {"token": settings["app_token"], "user": settings["user_key"],
              "title": title, "message": message, "priority": str(priority)}
    if url:
        fields["url"] = url
        fields["url_title"] = url_title or "Öffnen"
    request = urllib.request.Request(API_URL, data=urllib.parse.urlencode(fields).encode("utf-8"),
                                     method="POST")
    try:
        with (opener or urllib.request.urlopen)(request, timeout=SEND_TIMEOUT) as response:
            body = json.loads(response.read().decode("utf-8") or "{}")
        if body.get("status") == 1:
            return True, "", False
        return False, "; ".join(body.get("errors") or []) or "Pushover hat abgelehnt.", True
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8") or "{}")
            detail = "; ".join(body.get("errors") or [])
        except (ValueError, OSError):
            detail = ""
        permanent = 400 <= exc.code < 500 and exc.code != 429
        return False, detail or f"HTTP {exc.code}", permanent
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return False, f"Pushover nicht erreichbar: {getattr(exc, 'reason', exc)}", False


def _record(data_dir, user_id, now, ok, error, permanent, sent_keys=(), family_day=None, label=""):
    def mutate(state):
        state["last_result"] = {"at": now.isoformat(timespec="seconds"), "ok": ok,
                                "error": error, "what": label}
        if ok:
            state["sent"] = list(state.get("sent") or []) + list(sent_keys)
            if family_day:
                state["family_last"] = family_day
            state["halted"] = ""
        elif permanent:
            state["halted"] = error or "Pushover hat abgelehnt."
    update_state(data_dir, user_id, mutate)


def send_test(data_dir, user_id, now, *, opener=None):
    settings = load_settings(data_dir, user_id)
    url, url_title = deep_link(settings, "reminders")
    ok, error, permanent = send(settings, "Journl · Test", "Pushover ist eingerichtet.",
                                url=url, url_title=url_title, opener=opener)
    _record(data_dir, user_id, now, ok, error, permanent, label="Testnachricht")
    return ok, error


# ─── Verteilung (Scheduler) ───────────────────────────────────────────
def _parse_moment(value):
    """ISO-Zeitpunkt (DB liefert UTC, Einstellungen lokale Zeit) vergleichbar machen."""
    if isinstance(value, datetime):
        moment = value
    else:
        try:
            moment = datetime.fromisoformat(str(value or ""))
        except ValueError:
            return None
    return moment if moment.tzinfo else moment.astimezone()


def _analysis_batches(settings, state, notifications, now):
    """Faellige, noch nicht gemeldete Analyse-Hinweise je Schalter buendeln."""
    sent = set(state.get("sent") or [])
    enabled_at = _parse_moment(settings.get("enabled_at"))
    batches = {}
    for item in notifications:
        trigger = KIND_TRIGGER.get(item.get("kind") or "analysis")
        if not trigger or not settings["triggers"].get(trigger):
            continue
        key = f"analysis:{item.get('id')}"
        if key in sent:
            continue
        due = _parse_moment(item.get("notify_at") or item.get("created_at"))
        if enabled_at and due and due < enabled_at:
            continue
        batches.setdefault(trigger, []).append(key)
    return batches


def _reminder_moments(notifications, keys, now):
    """Faelligkeit (in der Zeitzone von ``now``) und Journaltag der gebuendelten Erinnerungen."""
    wanted = set(keys)
    moments = []
    for item in notifications:
        if f"analysis:{item.get('id')}" not in wanted:
            continue
        due = _parse_moment(item.get("notify_at"))
        if due is None:
            return None
        try:
            day = date.fromisoformat(str(item.get("run_date") or "")[:10])
        except ValueError:
            day = None
        moments.append((due.astimezone(now.tzinfo) if now.tzinfo else due, day))
    return moments


def _tagging_batches(settings, state, events):
    """Noch nicht gemeldete Job-Meldungen je Art: ``{art: (schluessel, summe)}``."""
    if not settings["triggers"].get("tagging"):
        return {}
    sent = set(state.get("sent") or [])
    enabled_at = _parse_moment(settings.get("enabled_at"))
    batches = {}
    for item in events:
        kind = item.get("kind")
        key = f"tagging:{item.get('id')}"
        if kind not in TAGGING_KINDS or key in sent:
            continue
        created = _parse_moment(item.get("created_at"))
        if enabled_at and created and created < enabled_at:
            continue
        keys, total = batches.get(kind, ([], 0))
        batches[kind] = (keys + [key], total + max(1, int(item.get("count") or 1)))
    return batches


def dispatch_user(data_dir, user_id, now, *, notifications=(), family_counts=None, opener=None,
                  tagging_events=()):
    """Ein Durchlauf fuer einen Nutzer. Gibt die Zahl gesendeter Nachrichten zurueck.

    ``notifications``: faellige, offene Analyse-Hinweise (Dicts mit id, kind,
    notify_at, created_at). ``family_counts``: ``(faellig, ueberfaellig)`` fuer
    heute oder ``None``, wenn nicht ermittelt.
    """
    settings = load_settings(data_dir, user_id)
    if not configured(settings):
        return 0
    state = load_state(data_dir, user_id)
    if state.get("halted") or in_quiet_hours(settings, now):
        return 0  # In der Ruhezeit bleibt alles offen und geht danach gebuendelt raus.
    count = 0
    # Fehler zuerst: sie tragen die hohe Prioritaet.
    batches = _analysis_batches(settings, state, notifications, now)
    for trigger in ("errors", "reminders", "results"):
        keys = batches.get(trigger)
        if not keys:
            continue
        moments = _reminder_moments(notifications, keys, now) if trigger == "reminders" else None
        title, message, priority = message_for(trigger, len(keys), moments=moments, now=now)
        url, url_title = deep_link(settings, trigger)
        ok, error, permanent = send(settings, title, message, priority=priority, url=url,
                                    url_title=url_title, opener=opener)
        _record(data_dir, user_id, now, ok, error, permanent, sent_keys=keys, label=title)
        if not ok:
            return count
        count += 1
    tagging = _tagging_batches(settings, state, tagging_events)
    for kind in TAGGING_KINDS:
        if kind not in tagging:
            continue
        keys, total = tagging[kind]
        title, message, priority = message_for(kind, total)
        url, url_title = deep_link(settings, kind)
        ok, error, permanent = send(settings, title, message, priority=priority, url=url,
                                    url_title=url_title, opener=opener)
        _record(data_dir, user_id, now, ok, error, permanent, sent_keys=keys, label=title)
        if not ok:
            return count
        count += 1
    today = now.date().isoformat()
    if (settings["triggers"].get("family") and family_counts and settings.get("family_time")
            and state.get("family_last") != today
            and now.time() >= _parse_time(settings["family_time"])):
        due, overdue = family_counts
        if due or overdue:
            title, message, priority = message_for("family", due, overdue)
            url, url_title = deep_link(settings, "family")
            ok, error, permanent = send(settings, title, message, priority=priority, url=url,
                                        url_title=url_title, opener=opener)
            _record(data_dir, user_id, now, ok, error, permanent, family_day=today if ok else None,
                    label=title)
            count += int(ok)
        else:
            # Nichts faellig: der Tag gilt trotzdem als erledigt, keine Nachricht.
            update_state(data_dir, user_id, lambda state: state.update(family_last=today))
    return count


def family_counts(documents, user_id, today):
    """``(heute faellig, ueberfaellig)`` offener Aufgaben, die der Nutzer sieht.

    Gleiche Sichtbarkeit wie das Board: zugaengliche Dokumente plus mir
    zugewiesene Aufgaben aus gesperrten. Einkaufslisten zaehlen nicht.
    """
    today = today.isoformat()
    due = overdue = 0
    for document, accessible in documents:
        if document.get("exclude_from_personal"):
            continue
        for task in document.get("tasks", []):
            if task.get("completed") or not task.get("target_date"):
                continue
            if not accessible and (task.get("user") or "") != user_id:
                continue
            if task["target_date"] == today:
                due += 1
            elif task["target_date"] < today:
                overdue += 1
    return due, overdue


def dispatch_all(data_dir, user_ids, now, *, notifications_for, documents_for=None, opener=None,
                 tagging_events_for=None):
    """Alle Nutzer mit eingerichtetem Pushover. Fehler eines Nutzers beenden
    nicht den Durchlauf; zurueck kommt die Zahl gesendeter Nachrichten."""
    total = 0
    for user_id in user_ids:
        settings = load_settings(data_dir, user_id)
        if not configured(settings):
            continue
        notifications = notifications_for(user_id) if any(
            settings["triggers"].get(name) for name in ("reminders", "results", "errors")) else []
        counts = None
        if settings["triggers"].get("family") and documents_for is not None:
            counts = family_counts(documents_for(user_id), user_id, now.date())
        events = tagging_events_for(user_id) if (
            tagging_events_for is not None and settings["triggers"].get("tagging")) else []
        total += dispatch_user(data_dir, user_id, now, notifications=notifications,
                               family_counts=counts, opener=opener, tagging_events=events)
    return total
