"""Einstellungen der semantischen Suche: global (Admin) und persoenlich.

AI maintenance contract: kein Flask, kein Modellaufruf. Zwei Dateien:

- ``DATA_DIR/semantic-search.json`` — global, nur Administratoren schreiben:
  Verbindung zum Embedding-Endpunkt, Nachtlauf, Batchgroesse, Mindestaehnlichkeit.
  Global, weil alle Collections mit demselben Modell und derselben Breite
  entstehen muessen; ein Modell pro Person haette eigene Neuaufbauten je Konto.
- ``DATA_DIR/<user>/.config/semantic-search.json`` — persoenlich: an/aus,
  vorgewaehlte Quellen, Reranker.

Der API-Schluessel verlaesst den Server nie (``public_*``); ein fehlendes Feld
heisst beim Speichern „unveraendert", ein leerer Wert loescht ihn.
"""

import json
import re
import sys

import agent_projects
from scheduling import read_text_file, write_text_file


GLOBAL_NAME = "semantic-search.json"
USER_NAME = "semantic-search.json"

SOURCES = ("journal", "notes", "family", "agent")
DEFAULT_SOURCES = ("journal", "notes")
DEFAULT_TIME = "03:00"
DEFAULT_BATCH = 8
# Gemessen (Kosinus, 1024 Dimensionen): wortnah ~0,65, Umschreibung ~0,40,
# unpassend ~0,19–0,22. 0,3 laesst Umschreibungen durch; das Eval kalibriert nach.
DEFAULT_MIN_SCORE = 0.3

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _core():
    return sys.modules.get("main") or sys.modules.get("__main__")


def _global_path():
    return _core().DATA_DIR / GLOBAL_NAME


def _user_path(user_id):
    return _core().DATA_DIR / user_id / ".config" / USER_NAME


def _load(path):
    if not path.is_file() or path.is_symlink():
        return {}
    try:
        stored = json.loads(read_text_file(path))
    except (OSError, json.JSONDecodeError):
        return {}
    return stored if isinstance(stored, dict) else {}


def _number(value, low, high, default, kind=int):
    if isinstance(value, bool):
        return default
    try:
        number = kind(value)
    except (TypeError, ValueError):
        return default
    return number if low <= number <= high else default


# ─── global ───────────────────────────────────────────────────────────
def load_global():
    stored = _load(_global_path())
    schedule_time = str(stored.get("schedule_time") or "")
    return {
        "base_url": str(stored.get("base_url") or ""),
        "model": str(stored.get("model") or ""),
        "api_key": str(stored.get("api_key") or ""),
        "job_enabled": bool(stored.get("job_enabled")),
        "schedule_time": schedule_time if _TIME_RE.match(schedule_time) else DEFAULT_TIME,
        "batch_size": _number(stored.get("batch_size"), 1, 32, DEFAULT_BATCH),
        "min_score": _number(stored.get("min_score"), 0.0, 1.0, DEFAULT_MIN_SCORE, float),
    }


def connection(settings):
    """Die Verbindung in der Form, die ``embeddings`` erwartet — oder ``None``."""
    if not (settings.get("base_url") and settings.get("model")):
        return None
    return {key: settings[key] for key in ("base_url", "model", "api_key")}


def public_global(settings):
    return {
        **{key: value for key, value in settings.items() if key != "api_key"},
        "has_api_key": bool(settings["api_key"]),
        "configured": connection(settings) is not None,
    }


def save_global(raw):
    """Teilaenderung pruefen und speichern; die Verbindung nur, wenn sie mitkommt."""
    if not isinstance(raw, dict):
        raise ValueError("Ungültige Einstellungen.")
    settings = load_global()
    if "base_url" in raw or "model" in raw:
        try:
            settings["base_url"] = agent_projects.validated_base_url(raw.get("base_url"))
        except agent_projects.AgentProjectError as exc:
            raise ValueError(str(exc)) from exc
        model = str(raw.get("model") or "").strip()
        if not agent_projects.valid_model_name(model):
            raise ValueError("Bitte ein Embedding-Modell wählen.")
        settings["model"] = model
    if "api_key" in raw:
        api_key = str(raw.get("api_key") or "").strip()
        if len(api_key) > 400:
            raise ValueError("Der API-Schlüssel ist zu lang.")
        settings["api_key"] = api_key
    if "job_enabled" in raw:
        settings["job_enabled"] = bool(raw.get("job_enabled"))
    if "schedule_time" in raw:
        value = str(raw.get("schedule_time") or "").strip()
        if not _TIME_RE.match(value):
            raise ValueError("Uhrzeit bitte als HH:MM angeben.")
        settings["schedule_time"] = value
    if "batch_size" in raw:
        value = _number(raw.get("batch_size"), 1, 32, None)
        if value is None:
            raise ValueError("Batchgröße muss zwischen 1 und 32 liegen.")
        settings["batch_size"] = value
    if "min_score" in raw:
        value = _number(raw.get("min_score"), 0.0, 1.0, None, float)
        if value is None:
            raise ValueError("Mindestähnlichkeit muss zwischen 0 und 1 liegen.")
        settings["min_score"] = round(value, 3)
    if settings["job_enabled"] and connection(settings) is None:
        raise ValueError("Für den Nachtlauf zuerst Endpunkt und Modell speichern.")
    write_text_file(_global_path(), json.dumps({"schema": 1, **settings}, ensure_ascii=False, indent=2) + "\n")
    return settings


# ─── persoenlich ──────────────────────────────────────────────────────
def _clean_sources(value):
    if not isinstance(value, list):
        return None
    picked = [source for source in SOURCES if source in value]
    return picked


def load_user(user_id):
    stored = _load(_user_path(user_id))
    sources = _clean_sources(stored.get("sources"))
    return {
        "enabled": bool(stored.get("enabled")),
        "sources": list(DEFAULT_SOURCES) if sources is None else sources,
        "rerank": bool(stored.get("rerank")),
    }


def save_user(user_id, raw):
    if not isinstance(raw, dict):
        raise ValueError("Ungültige Einstellungen.")
    settings = load_user(user_id)
    if "enabled" in raw:
        settings["enabled"] = bool(raw.get("enabled"))
    if "sources" in raw:
        sources = _clean_sources(raw.get("sources"))
        if sources is None:
            raise ValueError("Ungültige Quellenauswahl.")
        settings["sources"] = sources
    if "rerank" in raw:
        settings["rerank"] = bool(raw.get("rerank"))
    write_text_file(_user_path(user_id), json.dumps({"schema": 1, **settings}, ensure_ascii=False, indent=2) + "\n")
    return settings
