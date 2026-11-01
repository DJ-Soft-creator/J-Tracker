"""Player des Bildschirmschoners: eigene Musik, Podcasts und Internetradio.

Welche Quelle laeuft, sagt ``audio`` in den Einstellungen: ``music`` (die
Ordner unten), ``podcast`` (ein Abo aus ``screensaver_podcasts``) oder
``radio`` (ein Sender aus ``screensaver_radio``). ``/tracks`` liefert fuer
jede Quelle dieselbe Form, damit der Browser nur einen Player braucht.

Die Ordner sind dieselben wie fuer die Bilder - privat unter
``<user>/screensaver/ordner/<ordner>/`` (``user:<ordner>``), gemeinsam unter
``DATA_DIR/screensaver/<ordner>/`` (``local:<ordner>``). Was dort an Audio
liegt, ist ein Titel; ein Ordner kann zugleich Bild- und Musikquelle sein.

Ausgeliefert wird wie bei den Bildern nur, was ein Index benennt: je Konto ein
Verzeichnis aller erreichbaren Titel unter ``DATA_DIR/cache/screensaver/audio/``,
jederzeit loeschbar und bei einer unbekannten Kennung einmal neu gebaut. Die
Kennung ist ein Hash aus Art, Besitzer und Pfad - sie bleibt ueber Neuaufbauten
stabil, damit „Fortsetzen“ den Titel wiederfindet. Ein Pfad aus der Anfrage wird
nie verwendet. Titel, Interpret, Album und eingebettete Cover kommen aus den
Tags (``screensaver_tags``), sonst aus Dateiname und ``cover.jpg``.

Die Einstellungen liegen in ``user["screensaver_player"]`` (Whitelist wie beim
Schoner), der Wiedergabestand in ``<user>/screensaver/player.json``: der wird
waehrend des Hoerens alle paar Sekunden geschrieben und gehoert deshalb nicht in
den Nutzerdatensatz. Presets nehmen nur ``PRESET_FIELDS`` mit - Kuerzel, Ort und
Diktat-Verhalten sind Geraete- bzw. Gewohnheitsfragen, kein Setup.
"""

import mimetypes
import re
import time
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file

import screensaver
import screensaver_podcasts
import screensaver_radio
import screensaver_remote
import screensaver_tags


player_bp = Blueprint("screensaver_player", __name__, url_prefix="/api/screensaver/player")
# Als Unter-Blueprints: /api/screensaver/player/podcasts* und /radio*.
player_bp.register_blueprint(screensaver_podcasts.podcasts_bp)
player_bp.register_blueprint(screensaver_radio.radio_bp)

INDEX_FOLDER = "cache/screensaver/audio"
STATE_FILE = "screensaver/player.json"
INDEX_TTL = 600
TRACK_LIMIT = 10000
POSITION_MAX = 24 * 3600
CROSSFADE_MAX = 12
SKIP_CHOICES = (5, 10, 15, 20, 30, 45, 60)
COVER_STEMS = ("cover", "folder", "front", "album", "albumart")
_ID_RE = re.compile(r"^[0-9a-f]{40}$")
_TRACK_NUMBER_RE = re.compile(r"^\d{1,3}(?:\s*[-._)]\s*|\s+)(?=\S)")

PRESET_KEYS = 9
PRESET_ACTIONS = tuple(f"preset_{n}" for n in range(1, PRESET_KEYS + 1))
# „visual“ schaltet das Klangbild des Schoners reihum (screensaver-visual.js);
# es liegt hier, damit alle Kuerzel an einer Stelle belegt werden.
ACTIONS = ("play_pause", "back", "forward", "previous", "next", "volume_up", "volume_down", "visual") + PRESET_ACTIONS
DEFAULT_SHORTCUTS = {
    "play_pause": "Ctrl+Alt+Space",
    "back": "Ctrl+Alt+ArrowLeft",
    "forward": "Ctrl+Alt+ArrowRight",
    "previous": "Ctrl+Alt+Shift+ArrowLeft",
    "next": "Ctrl+Alt+Shift+ArrowRight",
    "volume_up": "Ctrl+Alt+ArrowUp",
    "volume_down": "Ctrl+Alt+ArrowDown",
    "visual": "Ctrl+Alt+KeyV",
    **{action: f"Ctrl+Alt+Digit{n}" for n, action in enumerate(PRESET_ACTIONS, start=1)},
}
# Schon vergeben: Strg+Alt+B startet den Schoner, K den Schreibmodus.
RESERVED_SHORTCUTS = {"Ctrl+Alt+KeyB", "Ctrl+Alt+KeyK"}
_MODIFIERS = ("Ctrl", "Alt", "Shift")
_KEY_RE = re.compile(
    r"^(?:Key[A-Z]|Digit[0-9]|Arrow(?:Left|Right|Up|Down)|Space|Enter|Home|End|PageUp|PageDown"
    r"|F(?:[1-9]|1[0-2])|Comma|Period|Slash|Semicolon|Quote|BracketLeft|BracketRight|Minus|Equal"
    r"|Backquote|Backslash|IntlBackslash)$"
)

# Lichteffekte, einzeln schaltbar. Voreinstellung: dezent und sparsam - die
# Farbe folgt dem Bild, Glas ist an; Analyser und Pulsieren erst auf Wunsch.
DEFAULT_EFFECTS = {"color": True, "reactive": False, "glass": True, "breathe": False}
DEFAULT_SETTINGS = {
    "audio": "music",
    "sources": [],
    "podcast": "",
    "station": "",
    "shuffle": False,
    "repeat": "folder",
    "crossfade": 0,
    "volume": 70,
    "muted": False,
    "visibility": "minimal",
    "autohide": False,
    "show_title": True,
    "show_cover": True,
    "display": "everywhere",
    "skip_back": 10,
    "skip_forward": 30,
    "dictation": "pause",
    "duck_level": 20,
    "shortcuts": dict(DEFAULT_SHORTCUTS),
    "effects": dict(DEFAULT_EFFECTS),
}
AUDIO_CHOICES = ("music", "podcast", "radio")
REPEAT_CHOICES = ("off", "folder", "track")
VISIBILITY_CHOICES = ("visible", "minimal", "hidden")
DISPLAY_CHOICES = ("screensaver", "write", "everywhere")
DICTATION_CHOICES = ("pause", "duck")
PRESET_FIELDS = ("audio", "sources", "podcast", "station", "shuffle", "repeat", "crossfade", "volume",
                 "muted", "visibility", "show_title", "show_cover", "effects")

MIME_TYPES = {
    ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".aac": "audio/aac", ".ogg": "audio/ogg",
    ".oga": "audio/ogg", ".opus": "audio/ogg", ".flac": "audio/flac", ".wav": "audio/wav",
}


# ─── Einstellungen ───────────────────────────────────────────────────

def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _shortcut(value):
    """Kuerzel in der Form ``Ctrl+Alt+Shift+<KeyboardEvent.code>`` oder ``None``.

    Ohne Strg oder Alt waere es eine Tipp-Taste; die Reihenfolge der
    Modifikatoren wird vereinheitlicht, damit der Vergleich im Browser simpel
    bleibt (Cmd zaehlt dort wie Strg)."""
    if not isinstance(value, str) or len(value) > 60:
        return None
    parts = value.split("+")
    key, modifiers = parts[-1], set(parts[:-1])
    if not _KEY_RE.match(key) or not modifiers <= set(_MODIFIERS) or not modifiers & {"Ctrl", "Alt"}:
        return None
    combo = "+".join([name for name in _MODIFIERS if name in modifiers] + [key])
    return None if combo in RESERVED_SHORTCUTS else combo


def _shortcuts(value):
    """Je Aktion eine Kombination; ``""`` heisst unbelegt.

    Doppelt belegt faellt die spaetere Aktion auf ihre Voreinstellung - ist
    auch die schon vergeben, bleibt sie unbelegt. Ausdruecklich ``""`` darf
    jede Aktion sein (etwa Preset 9, wenn Strg+Alt+9 woanders gebraucht wird)."""
    wanted = value if isinstance(value, dict) else {}
    result, taken = {}, set()
    for action in ACTIONS:
        raw = wanted.get(action)
        combo = "" if raw == "" else _shortcut(raw)
        if combo is None or combo in taken:
            combo = DEFAULT_SHORTCUTS[action]
        if combo in taken:
            combo = ""
        result[action] = combo
        if combo:
            taken.add(combo)
    return result


def _known_source(source):
    return isinstance(source, str) and source.split(":", 1)[0] in ("user", "local") \
        and screensaver._known_source(source)


def validate_settings(payload):
    """Eingaben auf die erlaubten Werte begrenzen; unbekanntes faellt weg."""
    data = payload if isinstance(payload, dict) else {}
    settings = {**DEFAULT_SETTINGS, "shortcuts": dict(DEFAULT_SHORTCUTS), "effects": dict(DEFAULT_EFFECTS)}

    sources = data.get("sources")
    if isinstance(sources, list):
        # Leer ist erlaubt: dann schweigt der Player einfach.
        settings["sources"] = list(dict.fromkeys(source for source in sources if _known_source(source)))

    # Podcast und Sender nur der Form nach - ob es sie (noch) gibt, meldet /tracks.
    for key in ("podcast", "station"):
        if isinstance(data.get(key), str) and (data[key] == "" or _ID_RE.match(data[key])):
            settings[key] = data[key]

    for key, choices in (("audio", AUDIO_CHOICES), ("repeat", REPEAT_CHOICES), ("visibility", VISIBILITY_CHOICES),
                         ("display", DISPLAY_CHOICES), ("dictation", DICTATION_CHOICES)):
        if data.get(key) in choices:
            settings[key] = data[key]

    for key in ("shuffle", "muted", "autohide", "show_title", "show_cover"):
        if isinstance(data.get(key), bool):
            settings[key] = data[key]

    for key, low, high in (("crossfade", 0, CROSSFADE_MAX), ("volume", 0, 100), ("duck_level", 0, 100)):
        if _number(data.get(key)):
            settings[key] = max(low, min(high, int(data[key])))

    for key in ("skip_back", "skip_forward"):
        if data.get(key) in SKIP_CHOICES and not isinstance(data.get(key), bool):
            settings[key] = int(data[key])

    if "shortcuts" in data:
        settings["shortcuts"] = _shortcuts(data["shortcuts"])
    if isinstance(data.get("effects"), dict):
        settings["effects"] = {key: data["effects"][key] if isinstance(data["effects"].get(key), bool) else value
                               for key, value in DEFAULT_EFFECTS.items()}
    return settings


def load_settings(user):
    return validate_settings(user.get("screensaver_player"))


def preset_snapshot(user):
    settings = load_settings(user)
    return {key: settings[key] for key in PRESET_FIELDS}


def apply_preset(user, stored):
    """Preset-Bereich ``player`` uebernehmen; liefert ``(settings, missing)``.

    Fehlt ein Musikordner inzwischen, spielt der Rest - das Preset behaelt ihn."""
    current = load_settings(user)
    picked = {key: value for key, value in stored.items() if key in PRESET_FIELDS}
    wanted = validate_settings({**current, **picked})
    known = {source["id"] for source in list_sources(user["id"])}
    missing = [source for source in wanted["sources"] if source not in known]
    if missing:
        wanted["sources"] = [source for source in wanted["sources"] if source in known]
    user["screensaver_player"] = wanted
    return wanted, missing


# ─── Quellen und Index ───────────────────────────────────────────────

def _roots(user_id):
    """Alle Musikwurzeln eines Kontos als ``(praefix, art, besitzer, wurzel)``."""
    return (
        ("user", "user", str(user_id), screensaver._user_collections_dir(user_id)),
        ("local", "local", "", screensaver._collections_dir()),
    )


def list_sources(user_id):
    """Ordner mit mindestens einem Titel, private zuerst."""
    items = []
    for prefix, kind, _owner, root in _roots(user_id):
        for folder in screensaver._folders(root):
            count = len(screensaver._folder_audio(folder))
            if count:
                items.append({"id": f"{prefix}:{folder.name}", "label": folder.name, "kind": kind, "count": count})
    return items


def _title(path, tags=None):
    if tags and tags.get("title"):
        return tags["title"]
    stem = path.stem.replace("_", " ").strip()
    return (_TRACK_NUMBER_RE.sub("", stem) or stem)[:160]


def _cover(directory, folder):
    """Erstes ``cover.jpg`` und Verwandte im Titelordner, sonst im Quellordner."""
    for place in (directory, folder):
        for suffix in (".jpg", ".jpeg", ".png", ".webp"):
            for stem in COVER_STEMS:
                for name in (stem + suffix, stem.capitalize() + suffix):
                    candidate = place / name
                    if candidate.is_file() and not candidate.is_symlink():
                        return candidate
        if place == folder:
            break
    return None


def _index_path(user_id):
    return screensaver._data_dir() / INDEX_FOLDER / f"{screensaver._digest(str(user_id))}.json"


def _track_id(kind, owner, relative):
    return screensaver._digest(f"audio:{kind}:{owner}:{relative}")


def _build_index(user_id):
    tracks = {}
    covers = {}
    for prefix, kind, owner, root in _roots(user_id):
        tag_cache = screensaver_tags.TagCache(kind, owner)
        for folder in screensaver._folders(root):
            for path in screensaver._folder_audio(folder):
                if len(tracks) >= TRACK_LIMIT:
                    break
                relative = path.relative_to(root).as_posix()
                if path.parent not in covers:
                    cover = _cover(path.parent, folder)
                    covers[path.parent] = cover.relative_to(root).as_posix() if cover else ""
                tags = tag_cache.get(path, relative)
                album = tags.get("album") or (path.parent.name if path.parent != folder else "")
                tracks[_track_id(kind, owner, relative)] = {
                    "kind": kind,
                    "owner": owner,
                    "source": f"{prefix}:{folder.name}",
                    "path": relative,
                    "cover": covers[path.parent],
                    "art": bool(tags.get("art")),
                    "title": _title(path, tags),
                    "artist": tags.get("artist", ""),
                    "album": album[:120],
                    "label": folder.name,
                }
        tag_cache.save()
    payload = {"tracks": tracks, "built": int(time.time())}
    try:
        screensaver._write_json(_index_path(user_id), payload)
    except OSError:
        pass  # Ohne Cache wird beim naechsten Abruf neu gezaehlt, mehr nicht.
    return tracks


def _index(user_id, fresh=False):
    if not fresh:
        cached = screensaver._read_json(_index_path(user_id), max_age=INDEX_TTL)
        if cached and isinstance(cached.get("tracks"), dict):
            return cached["tracks"]
    return _build_index(user_id)


def _entry(user_id, track_id):
    """Indexeintrag zu einer Kennung; bei Unbekanntem einmal frisch gezaehlt."""
    if not _ID_RE.match(track_id or ""):
        return None
    entry = _index(user_id).get(track_id)
    if entry is None:
        entry = _index(user_id, fresh=True).get(track_id)
    return entry if isinstance(entry, dict) else None


def _resolve(user_id, entry, field, suffixes):
    """Pfad aus dem Index gegen seine erlaubte Wurzel pruefen."""
    relative = entry.get(field)
    if not isinstance(relative, str) or not relative:
        return None
    if entry.get("kind") == "user":
        if entry.get("owner") != str(user_id):
            return None
        root = screensaver._user_collections_dir(user_id)
    elif entry.get("kind") == "local":
        root = screensaver._collections_dir()
    else:
        return None
    path = root / relative
    try:
        resolved = path.resolve()
        resolved.relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    if path.is_symlink() or not resolved.is_file() or resolved.suffix.lower() not in suffixes:
        return None
    return resolved


def tracks_for(user_id, sources):
    """Titel der gewaehlten Quellen: Quellen in Auswahlreihenfolge, innen nach Pfad.

    Gemischt wird im Browser - so bleiben Shuffle und „Ordner wiederholen“
    eine Frage der Wiedergabe, nicht des Servers."""
    index = _index(user_id)
    wanted = set(sources)
    if not wanted <= {entry.get("source") for entry in index.values()}:
        # Ein neuer Ordner seit dem letzten Zaehlen: lieber einmal frisch.
        index = _index(user_id, fresh=True)
    rank = {source: position for position, source in enumerate(sources)}
    picked = [(track_id, entry) for track_id, entry in index.items() if entry.get("source") in wanted]
    picked.sort(key=lambda item: (rank[item[1]["source"]], item[1]["path"].lower()))
    return [{
        "id": track_id,
        "src": f"/api/screensaver/player/audio/{track_id}",
        "cover": f"/api/screensaver/player/cover/{track_id}" if entry.get("cover") or entry.get("art") else "",
        "title": entry.get("title", ""),
        "artist": entry.get("artist", ""),
        "album": entry.get("album", ""),
        "source": entry.get("label", ""),
        "source_id": entry.get("source", ""),
    } for track_id, entry in picked]


# ─── Wiedergabestand ─────────────────────────────────────────────────

def _state_path(user_id):
    return screensaver._user_root(user_id) / STATE_FILE


def _stored_states(user_id):
    """Stand je Quelle; die Fassung vor Podcast/Radio kannte nur Musik."""
    stored = screensaver._read_json(_state_path(user_id)) or {}
    if "track" in stored:
        stored = {"music": stored}
    return {mode: stored[mode] for mode in AUDIO_CHOICES if isinstance(stored.get(mode), dict)}


def load_state(user_id, mode="music"):
    stored = _stored_states(user_id).get(mode, {})
    track = stored.get("track")
    position = stored.get("position")
    return {
        "track": track if isinstance(track, str) and _ID_RE.match(track) else "",
        "position": float(position) if _number(position) and 0 <= position <= POSITION_MAX else 0.0,
        "updated": stored.get("updated") if _number(stored.get("updated")) else 0,
    }


def save_state(user_id, payload):
    """Letzten Titel und Position je Quelle merken; bei Podcasts auch je Episode."""
    data = payload if isinstance(payload, dict) else {}
    track = data.get("track")
    mode = data.get("mode") if data.get("mode") in AUDIO_CHOICES else "music"
    if not isinstance(track, str) or not _ID_RE.match(track):
        return None
    position = data.get("position")
    position = max(0.0, min(float(POSITION_MAX), float(position))) if _number(position) else 0.0
    state = {"track": track, "position": round(position, 1), "updated": int(time.time())}
    states = _stored_states(user_id)
    states[mode] = state
    screensaver._write_json(_state_path(user_id), states)
    if mode == "podcast":
        duration = data.get("duration") if _number(data.get("duration")) else 0
        state["episode"] = screensaver_podcasts.save_progress(user_id, track, position, duration)
    return state


# ─── Routen ──────────────────────────────────────────────────────────

@player_bp.route("/settings", methods=["GET"])
def get_settings():
    user, error = screensaver._browser_user()
    if error:
        return error
    payload = {"settings": load_settings(user)}
    # Die Quellenliste zaehlt Ordner - nur auf Nachfrage (Einstellungsmaske).
    if request.args.get("sources") == "1":
        payload["sources"] = list_sources(user["id"])
    return jsonify(payload)


@player_bp.route("/settings", methods=["POST"])
def set_settings():
    user, error = screensaver._guarded_user()
    if error:
        return error
    settings = validate_settings(request.get_json(silent=True) or {})
    user["screensaver_player"] = settings
    screensaver._main().update_user(user)
    return jsonify({"ok": True, "settings": settings})


@player_bp.route("/tracks", methods=["GET"])
def tracks():
    """Titel der gewaehlten Quelle in einer Form: Musik, Episoden oder Sender."""
    user, error = screensaver._browser_user()
    if error:
        return error
    settings = load_settings(user)
    mode = settings["audio"]
    payload = {"mode": mode, "state": load_state(user["id"], mode), "missing": [], "rate": 1.0, "error": ""}
    if mode == "podcast":
        items, payload["rate"], problem = screensaver_podcasts.tracks_for(user["id"], settings["podcast"])
        if problem == "missing":
            payload["missing"] = [settings["podcast"]]
        else:
            payload["error"] = problem
    elif mode == "radio":
        items = screensaver_radio.tracks_for(user["id"])
        if settings["station"] and not any(item["id"] == settings["station"] for item in items):
            payload["missing"] = [settings["station"]]
        # Der gewaehlte Sender ist der Stand - so startet ein Preset seinen Sender.
        payload["state"] = {**payload["state"], "track": settings["station"] or payload["state"]["track"]}
    else:
        items = tracks_for(user["id"], settings["sources"])
        present = {item["source_id"] for item in items}
        payload["missing"] = [source for source in settings["sources"] if source not in present]
    return jsonify({**payload, "tracks": items})


@player_bp.route("/audio/<track_id>", methods=["GET"])
def audio(track_id):
    user, error = screensaver._browser_user()
    if error:
        return error
    entry = _entry(user["id"], track_id)
    path = _resolve(user["id"], entry, "path", screensaver.AUDIO_SUFFIXES) if entry else None
    if path is None:
        return jsonify({"error": "Unbekannter Titel"}), 404
    mimetype = MIME_TYPES.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    # conditional=True liefert Range-Antworten (206) - ohne sie liesse sich nicht spulen.
    return send_file(path, mimetype=mimetype, conditional=True, max_age=3600)


@player_bp.route("/cover/<track_id>", methods=["GET"])
def cover(track_id):
    user, error = screensaver._browser_user()
    if error:
        return error
    entry = _entry(user["id"], track_id)
    if entry and entry.get("art"):
        # Das eingebettete Cover gehoert zum Titel, das im Ordner nur zum Album.
        audio_path = _resolve(user["id"], entry, "path", screensaver.AUDIO_SUFFIXES)
        art = screensaver_tags.cover(audio_path, track_id) if audio_path else None
        if art:
            return send_file(art[0], mimetype=art[1], max_age=3600, conditional=True)
    path = _resolve(user["id"], entry, "cover", screensaver.IMAGE_SUFFIXES) if entry else None
    if path is None:
        return jsonify({"error": "Kein Cover"}), 404
    return send_file(path, max_age=3600, conditional=True)


@player_bp.route("/art/<art_id>", methods=["GET"])
def art(art_id):
    """Cover eines Podcasts oder Logo eines Senders - nur aus eigenen Abos/Sendern."""
    user, error = screensaver._browser_user()
    if error:
        return error
    url = None
    if _ID_RE.match(art_id or ""):
        url = screensaver_podcasts.known_art(user["id"], art_id) or screensaver_radio.known_art(user["id"], art_id)
    cached = screensaver_remote.cached_image(url, art_id) if url else None
    if cached is None:
        return jsonify({"error": "Kein Bild"}), 404
    return send_file(cached[0], mimetype=cached[1], max_age=86400, conditional=True)


@player_bp.route("/state", methods=["PUT"])
def put_state():
    user, error = screensaver._guarded_user()
    if error:
        return error
    try:
        state = save_state(user["id"], request.get_json(silent=True) or {})
    except OSError:
        return jsonify({"error": "Stand nicht gespeichert."}), 500
    if state is None:
        return jsonify({"error": "Unbekannter Titel"}), 400
    return jsonify({"ok": True, "state": state})


__all__ = ["player_bp", "validate_settings", "load_settings", "preset_snapshot", "apply_preset",
           "list_sources", "tracks_for", "PRESET_FIELDS", "DEFAULT_SHORTCUTS", "PRESET_ACTIONS"]
