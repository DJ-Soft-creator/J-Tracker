"""Bildschirmschoner: Vollflaechige Diashow als Schreib- und Diktierkulisse.

Drei Quellenarten, eine Mechanik:

* **Thema** — kuratierte Kategorien von Wikimedia Commons (``theme:<slug>``).
  Der Server holt die Bilder, nicht der Browser: die CSP bleibt bei
  ``img-src 'self'``, das Endgeraet spricht nie mit einem fremden Host und die
  Diashow laeuft auch offline aus dem Cache weiter.
* **Gemeinsamer Ordner** — jeder Unterordner unter ``DATA_DIR/screensaver/`` ist
  eine einzeln waehlbare Sammlung fuer alle Konten (``local:<ordner>``).
* **Privater Ordner** — Unterordner unter ``<user>/screensaver/ordner/`` sieht
  nur ihr Besitzer (``user:<ordner>``). Angelegt, umbenannt und befuellt werden
  sie ueber ``/api/screensaver/folders`` oder direkt im Dateisystem.
* **Journalbilder** — die eigenen Medien unter ``<user>/YYYY/MM/DD/media/``
  (``journal``).

Die Ordner tragen Bilder und Audio nebeneinander; der Typ folgt der Endung.
Musik daraus spielt ``screensaver_player``, die Verwaltung hier nimmt beides an.

Der Cache unter ``DATA_DIR/cache/screensaver/`` ist reiner Beschleuniger und
jederzeit loeschbar: ``<id>.json`` haelt Herkunft und Bildnachweis, ``<id>.bin``
die heruntergebildeten Bytes. Nur diese Manifeste entscheiden, welche Datei
``/api/screensaver/image/<id>`` ausliefert — ein Pfad oder eine URL aus der
Anfrage wird nie verwendet.
"""

import hashlib
import json
import os
import random
import re
import shutil
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file

import screensaver_visual


screensaver_bp = Blueprint("screensaver", __name__, url_prefix="/api/screensaver")

COLLECTIONS_FOLDER = "screensaver"
USER_COLLECTIONS_FOLDER = "screensaver/ordner"
CACHE_FOLDER = "cache/screensaver"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
IMAGE_WIDTH = 1920
DOWNLOAD_TIMEOUT = 20
MAX_IMAGE_BYTES = 16 * 1024 * 1024
# Uploads bleiben im Original; die Grenze schuetzt nur die Platte.
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_AUDIO_UPLOAD_BYTES = 500 * 1024 * 1024
UPLOAD_CHUNK = 1024 * 1024
CATEGORY_TTL = 24 * 3600
LOCAL_INDEX_TTL = 600
CACHE_LIMIT = 400
PLAYLIST_MAX = 40
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".avif", ".gif"}
AUDIO_SUFFIXES = {".mp3", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".flac", ".wav"}
MEDIA_SUFFIXES = IMAGE_SUFFIXES | AUDIO_SUFFIXES
_ID_RE = re.compile(r"^[0-9a-f]{40}$")
_FOLDER_RE = re.compile(r"^[^/\\.][^/\\]{0,79}$")
_TAG_RE = re.compile(r"<[^>]+>")

# Verifiziert gegen die Commons-API; leere Kategorien werden beim Sammeln
# einfach uebersprungen, damit ein Umbenennen dort die Diashow nicht anhaelt.
THEMES = {
    "landscapes": ("Landschaften", [
        "Featured pictures of landscapes",
        "Quality images of landscapes",
    ]),
    "mountains": ("Berge & Vulkane", [
        "Featured pictures of mountains",
        "Featured pictures of volcanoes",
    ]),
    "water": ("Meer & Gewässer", [
        "Featured pictures of coasts",
        "Featured pictures of bodies of water",
        "Featured pictures of islands",
    ]),
    "forests": ("Wälder, Parks & Gärten", [
        "Featured pictures of forests",
        "Featured pictures of parks",
        "Featured pictures of gardens",
    ]),
    "astronomy": ("Himmel & Astronomie", [
        "Featured pictures of astronomy",
    ]),
    "architecture": ("Architektur", [
        "Featured pictures of architecture",
    ]),
    "caves": ("Höhlen", [
        "Featured pictures of caves",
    ]),
}

DEFAULT_SETTINGS = {
    "interval": 30,
    "sources": ["theme:landscapes"],
    "info": "full",
    "transition": "fade",
    "fade_seconds": 1.0,
    "dim": 20,
    "mode": "screensaver",
    "order": "shuffle",
    "details": False,
    "visual": screensaver_visual.DEFAULT_VISUAL,
}
INFO_CHOICES = ("full", "clock", "image", "off")
TRANSITION_CHOICES = ("fade", "cut", "kenburns")
MODE_CHOICES = ("screensaver", "focus", "questions")
# shuffle: alles gemischt · spread: Quellen wechseln sich gleichmaessig ab ·
# sequential: der Reihe nach (Journalbilder chronologisch), blaettert per offset.
ORDER_CHOICES = ("shuffle", "spread", "sequential")
SEQUENCE_LIMIT = 5000
INTERVAL_MIN = 5
INTERVAL_MAX = 900
FADE_MAX = 10.0
FADE_STEP = 0.1


def _main():
    return sys.modules.get("main") or sys.modules.get("__main__")


def _data_dir():
    return _main().DATA_DIR


def _browser_user():
    user = _main()._current_user()
    return (user, None) if user else (None, (jsonify({"error": "Unauthorized"}), 401))


def _csrf_error():
    return _main().csrf_protect(lambda: None)()


def _cache_dir():
    return _data_dir() / CACHE_FOLDER


def _collections_dir():
    return _data_dir() / COLLECTIONS_FOLDER


def _user_root(user_id):
    return _data_dir() / str(user_id)


def _user_collections_dir(user_id):
    return _user_root(user_id) / USER_COLLECTIONS_FOLDER


def _digest(value):
    return hashlib.sha1(value.encode("utf-8")).hexdigest()


def _atomic_write(path, data, mode="wb"):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open(mode) as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_json(path, max_age=None):
    if not path.is_file() or path.is_symlink():
        return None
    if max_age is not None and time.time() - path.stat().st_mtime > max_age:
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _write_json(path, payload):
    _atomic_write(path, json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n", mode="w")


# ─── Einstellungen ───────────────────────────────────────────────────

def validate_settings(payload):
    """Eingaben auf die erlaubten Werte begrenzen; unbekanntes faellt weg."""
    data = payload if isinstance(payload, dict) else {}
    settings = dict(DEFAULT_SETTINGS)

    interval = data.get("interval")
    if isinstance(interval, (int, float)) and not isinstance(interval, bool):
        settings["interval"] = max(INTERVAL_MIN, min(INTERVAL_MAX, int(interval)))

    sources = data.get("sources")
    if isinstance(sources, list):
        cleaned = [source for source in sources if _known_source(source)]
        if len(cleaned) > 1:
            cleaned = [source for source in cleaned if source != screensaver_visual.NO_IMAGES]
        # Ohne Quelle bliebe der Schoner schwarz; die Voreinstellung springt ein.
        settings["sources"] = list(dict.fromkeys(cleaned)) or list(DEFAULT_SETTINGS["sources"])

    for key, choices in (("info", INFO_CHOICES), ("transition", TRANSITION_CHOICES), ("mode", MODE_CHOICES),
                         ("order", ORDER_CHOICES)):
        if data.get(key) in choices:
            settings[key] = data[key]

    fade = data.get("fade_seconds")
    if isinstance(fade, (int, float)) and not isinstance(fade, bool):
        # Auf Zehntel gerastert, damit der Regler und der gespeicherte Wert
        # dieselbe Schrittweite haben.
        settings["fade_seconds"] = round(max(0.0, min(FADE_MAX, float(fade))) / FADE_STEP) * FADE_STEP
        settings["fade_seconds"] = round(settings["fade_seconds"], 1)

    if isinstance(data.get("details"), bool):
        settings["details"] = data["details"]

    dim = data.get("dim")
    if isinstance(dim, (int, float)) and not isinstance(dim, bool):
        settings["dim"] = max(0, min(80, int(dim)))
    settings["visual"] = screensaver_visual.validate(data.get("visual"))
    return settings


def _known_source(source):
    if not isinstance(source, str):
        return False
    if source in ("journal", screensaver_visual.NO_IMAGES):
        return True
    if source.startswith("theme:"):
        return source[6:] in THEMES
    if source.startswith("local:") or source.startswith("user:"):
        return bool(_FOLDER_RE.match(source.split(":", 1)[1]))
    return False


def load_settings(user):
    return validate_settings(user.get("screensaver"))


# ─── Quellen ─────────────────────────────────────────────────────────

def list_sources(user_id):
    """Alles, was sich als Quelle anwaehlen laesst — mit Bildanzahl, wo bekannt."""
    items = [{"id": screensaver_visual.NO_IMAGES, "label": "Keine Bilder", "kind": "none"}]
    items += [
        {"id": f"theme:{slug}", "label": label, "kind": "theme"}
        for slug, (label, _categories) in THEMES.items()
    ]
    for folder in _folders(_user_collections_dir(user_id)):
        items.append({
            "id": f"user:{folder.name}",
            "label": folder.name,
            "kind": "user",
            "count": len(_folder_images(folder)),
        })
    for folder in _folders(_collections_dir()):
        items.append({
            "id": f"local:{folder.name}",
            "label": folder.name,
            "kind": "local",
            "count": len(_folder_images(folder)),
        })
    items.append({
        "id": "journal",
        "label": "Meine Journalbilder",
        "kind": "journal",
        "count": len(_journal_images(user_id)),
    })
    return items


def _folders(root):
    """Sichtbare Unterordner einer Wurzel, alphabetisch; Dateien zaehlen nicht."""
    try:
        if not root.is_dir() or root.is_symlink():
            return []
        return sorted((folder for folder in root.iterdir()
                       if folder.is_dir() and not folder.is_symlink() and _FOLDER_RE.match(folder.name)),
                      key=lambda path: path.name.lower())
    except OSError:
        return []


def _folder_files(folder, suffixes):
    """Dateien eines Ordnerbaums mit passender Endung; Symlinks und alles
    Versteckte (auch halbfertige Uploads) bleiben draussen."""
    try:
        return sorted(
            path for path in folder.rglob("*")
            if path.is_file() and not path.is_symlink() and path.suffix.lower() in suffixes
            and not any(part.startswith(".") for part in path.relative_to(folder).parts)
        )
    except OSError:
        return []


def _folder_images(folder):
    return _folder_files(folder, IMAGE_SUFFIXES)


def _folder_audio(folder):
    return _folder_files(folder, AUDIO_SUFFIXES)


def _journal_images(user_id):
    """Eigene Journalmedien, zwischengespeichert — ein rglob je zehn Minuten."""
    cache_path = _cache_dir() / f"index-journal-{_digest(str(user_id))}.json"
    cached = _read_json(cache_path, max_age=LOCAL_INDEX_TTL)
    if cached and isinstance(cached.get("paths"), list):
        return cached["paths"]

    root = _user_root(user_id)
    paths = []
    try:
        for path in root.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/[0-9][0-9]/media/**/*"):
            if not path.is_file() or path.is_symlink() or path.suffix.lower() not in IMAGE_SUFFIXES:
                continue
            paths.append(str(path.relative_to(root)))
            if len(paths) >= 4000:
                break
    except OSError:
        paths = []
    paths.sort()
    try:
        _write_json(cache_path, {"paths": paths})
    except OSError:
        pass  # Ohne Cache dauert der naechste Aufbau laenger, mehr nicht.
    return paths


# ─── Wikimedia Commons ───────────────────────────────────────────────

def _commons_request(params):
    query = urllib.parse.urlencode({**params, "format": "json", "action": "query"})
    version = "unknown"
    try:
        version = _main().load_app_version()
    except Exception:  # pragma: no cover - Version ist nur Hoeflichkeit im Header
        pass
    call = urllib.request.Request(
        f"{COMMONS_API}?{query}",
        headers={"User-Agent": f"Journl/{version} (self-hosted personal journal)"},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(call, timeout=DOWNLOAD_TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


def _category_titles(category):
    """Dateinamen einer Kategorie, einen Tag lang zwischengespeichert.

    Commons drosselt schnelle Folgeabfragen; ohne diesen Cache liefe die
    Diashow beim Bilderwechsel in leere Antworten.
    """
    cache_path = _cache_dir() / f"category-{_digest(category)}.json"
    cached = _read_json(cache_path, max_age=CATEGORY_TTL)
    if cached and isinstance(cached.get("titles"), list):
        return cached["titles"]
    try:
        payload = _commons_request({
            "list": "categorymembers",
            "cmtitle": f"Category:{category}",
            "cmtype": "file",
            "cmlimit": 500,
        })
    except Exception:
        # Eine alte Liste ist besser als keine: der Cache wird ohne TTL gelesen.
        stale = _read_json(cache_path)
        return stale.get("titles", []) if stale else []
    titles = [
        member["title"] for member in payload.get("query", {}).get("categorymembers", [])
        if isinstance(member.get("title"), str)
    ]
    if titles:
        try:
            _write_json(cache_path, {"titles": titles})
        except OSError:
            pass
    return titles


def _plain(value):
    return _TAG_RE.sub("", value or "").replace("&amp;", "&").strip()


def _commons_details(titles):
    """Bild-URL und Bildnachweis fuer bis zu 50 Dateien in einem Aufruf."""
    if not titles:
        return []
    try:
        payload = _commons_request({
            "titles": "|".join(titles[:50]),
            "prop": "imageinfo",
            "iiprop": "url|extmetadata",
            "iiurlwidth": IMAGE_WIDTH,
        })
    except Exception:
        return []
    items = []
    for page in payload.get("query", {}).get("pages", {}).values():
        info = (page.get("imageinfo") or [{}])[0]
        url = info.get("thumburl") or info.get("url")
        if not isinstance(url, str) or not url.startswith("https://"):
            continue
        meta = info.get("extmetadata") or {}

        def field(name):
            entry = meta.get(name)
            return _plain(entry.get("value")) if isinstance(entry, dict) else ""

        title = field("ObjectName") or re.sub(r"^File:|\.[A-Za-z0-9]+$", "", page.get("title", ""))
        items.append({
            "kind": "remote",
            "url": url,
            "page_title": page.get("title", ""),
            "title": title[:120],
            "author": field("Artist")[:80],
            "license": field("LicenseShortName")[:40],
            "link": info.get("descriptionurl") or "",
            "source": "Wikimedia Commons",
            "description": field("ImageDescription")[:400],
            "date": field("DateTimeOriginal")[:40],
        })
    return items


# ─── Playlist ────────────────────────────────────────────────────────

def build_playlist(user_id, sources, count, order="shuffle", offset=0):
    """Bildliste ueber alle gewaehlten Quellen; liefert ``(items, next_offset)``.

    Fuer jedes Bild entsteht ein Manifest im Cache; die Antwort verweist nur
    auf dessen Kennung, nie auf einen Pfad oder eine fremde URL.
    """
    count = max(1, min(PLAYLIST_MAX, int(count)))
    if screensaver_visual.no_images(sources):
        return [], 0
    selected = [source for source in sources if _known_source(source) and source != screensaver_visual.NO_IMAGES] \
        or list(DEFAULT_SETTINGS["sources"])
    if order == "sequential":
        candidates, next_offset = _sequential_candidates(user_id, selected, count, max(0, int(offset or 0)))
    else:
        per_source = max(2, -(-count // max(1, len(selected))) + 2)
        groups = [_source_candidates(user_id, source, per_source) for source in selected]
        if order == "spread":
            # Reihum aus jeder Quelle eines - eine grosse Sammlung verdraengt
            # die kleinen nicht mehr.
            candidates = [group[index] for index in range(max((len(g) for g in groups), default=0))
                          for group in groups if index < len(group)]
        else:
            candidates = [item for group in groups for item in group]
            random.shuffle(candidates)
        next_offset = 0
    items = []
    seen = set()
    for candidate in candidates:
        key = candidate.get("url") or candidate.get("path")
        if not key or key in seen:
            continue
        seen.add(key)
        items.append(_publish(candidate))
        if len(items) >= count:
            break
    _prune_cache()
    return items, next_offset


def _source_files(user_id, source):
    """Alle Dateien einer lokalen Quelle als ``(absolut, relativ)``, sortiert."""
    if source == "journal":
        return [(_user_root(user_id) / relative, relative) for relative in _journal_images(user_id)]
    prefix, name = source.split(":", 1)
    root = _user_collections_dir(user_id) if prefix == "user" else _collections_dir()
    folder = root / name
    if not folder.is_dir() or folder.is_symlink():
        return []
    return [(path, str(path.relative_to(root))) for path in _folder_images(folder)]


def _source_identity(user_id, source):
    """Manifest-Art, Besitzer und Anzeigename einer lokalen Quelle."""
    if source == "journal":
        return "journal", str(user_id), "Mein Journal"
    prefix, name = source.split(":", 1)
    return ("user", str(user_id), name) if prefix == "user" else ("local", "", name)


def _source_candidates(user_id, source, wanted):
    if source.startswith("theme:"):
        return _theme_candidates(source[6:], wanted)
    return _local_candidates(_source_files(user_id, source), wanted, *_source_identity(user_id, source))


def _sequential_candidates(user_id, sources, count, offset):
    """Der Reihe nach: Quellen nacheinander, innen nach Pfad (Journal = Datum)."""
    sequence = []
    for source in sources:
        if source.startswith("theme:"):
            label, categories = THEMES.get(source[6:], (None, []))
            titles = sorted({title for category in categories for title in _category_titles(category)})
            sequence.extend(("theme", source[6:], title) for title in titles)
        else:
            sequence.extend(("file", source, entry) for entry in _source_files(user_id, source))
        if len(sequence) >= SEQUENCE_LIMIT:
            break
    if not sequence:
        return [], 0
    if offset >= len(sequence):
        offset = 0
    window = sequence[offset:offset + count]
    candidates = []
    remote = [entry for entry in window if entry[0] == "theme"]
    details = {item.get("page_title"): item for item in _commons_details([entry[2] for entry in remote])}
    for kind, source, value in window:
        if kind == "theme":
            item = details.get(value)
            if item:
                candidates.append({**item, "theme": THEMES[source][0]})
        else:
            candidates.extend(_local_candidates([value], 1, *_source_identity(user_id, source)))
    next_offset = offset + len(window)
    return candidates, (0 if next_offset >= len(sequence) else next_offset)


def _local_candidates(files, wanted, kind, owner, label):
    if not files:
        return []
    picked = random.sample(files, min(wanted, len(files)))
    return [{
        "kind": kind,
        "path": relative,
        "owner": owner,
        "title": Path(relative).stem[:120],
        "author": "",
        "license": "",
        "link": "",
        "source": label,
        "date": _path_date(relative) if kind == "journal" else "",
        "description": "",
    } for _absolute, relative in picked]


_PATH_DATE_RE = re.compile(r"^(\d{4})/(\d{2})/(\d{2})/")


def _path_date(relative):
    match = _PATH_DATE_RE.match(str(relative))
    return f"{match.group(3)}.{match.group(2)}.{match.group(1)}" if match else ""


def _theme_candidates(slug, wanted):
    label, categories = THEMES.get(slug, (None, []))
    if not label:
        return []
    titles = []
    for category in categories:
        titles.extend(_category_titles(category))
    if not titles:
        return []
    picked = random.sample(titles, min(wanted, len(titles)))
    details = _commons_details(picked)
    for item in details:
        item["theme"] = label
    return details


def _publish(candidate):
    """Manifest ablegen und die oeffentliche Beschreibung des Bildes bauen."""
    key = candidate.get("url") or f"{candidate['kind']}:{candidate.get('owner', '')}:{candidate['path']}"
    image_id = _digest(key)
    manifest_path = _cache_dir() / f"{image_id}.json"
    if not manifest_path.is_file():
        try:
            _write_json(manifest_path, candidate)
        except OSError:
            pass
    else:
        os.utime(manifest_path, None)  # Frisch benutzte Bilder ueberleben das Aufraeumen.
    return {
        "id": image_id,
        "src": f"/api/screensaver/image/{image_id}",
        "title": candidate.get("title", ""),
        "author": candidate.get("author", ""),
        "license": candidate.get("license", ""),
        "link": candidate.get("link", ""),
        "source": candidate.get("theme") or candidate.get("source", ""),
        "description": candidate.get("description", ""),
        "date": candidate.get("date", ""),
    }


def _prune_cache():
    """Aelteste Bilder verwerfen, sobald der Cache ueber die Grenze waechst."""
    directory = _cache_dir()
    if not directory.is_dir():
        return
    try:
        manifests = [path for path in directory.glob("*.json") if path.is_file()]
    except OSError:
        return
    if len(manifests) <= CACHE_LIMIT:
        return
    manifests.sort(key=lambda path: path.stat().st_mtime)
    for path in manifests[:len(manifests) - CACHE_LIMIT]:
        path.with_suffix(".bin").unlink(missing_ok=True)
        path.unlink(missing_ok=True)


# ─── Auslieferung ────────────────────────────────────────────────────

def _safe_local_path(manifest, user_id):
    """Pfad aus dem Manifest gegen seine erlaubte Wurzel pruefen."""
    relative = manifest.get("path")
    if not isinstance(relative, str):
        return None
    if manifest.get("kind") in ("journal", "user"):
        if str(manifest.get("owner")) != str(user_id):
            return None  # Journalbilder und private Ordner bleiben beim eigenen Konto.
        root = _user_root(user_id) if manifest["kind"] == "journal" else _user_collections_dir(user_id)
    else:
        root = _collections_dir()
    try:
        resolved = (root / relative).resolve()
        resolved.relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    if not resolved.is_file() or resolved.suffix.lower() not in IMAGE_SUFFIXES:
        return None
    return resolved


def _download(url, target):
    call = urllib.request.Request(url, headers={"User-Agent": "Journl (self-hosted personal journal)"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(call, timeout=DOWNLOAD_TIMEOUT) as response:
        if not (response.headers.get("Content-Type") or "").startswith("image/"):
            return False
        data = response.read(MAX_IMAGE_BYTES + 1)
    if not data or len(data) > MAX_IMAGE_BYTES:
        return False
    _atomic_write(target, data)
    return True


@screensaver_bp.route("/settings", methods=["GET"])
def get_settings():
    user, error = _browser_user()
    if error:
        return error
    return jsonify({"settings": load_settings(user), "sources": list_sources(user["id"])})


@screensaver_bp.route("/settings", methods=["POST"])
def set_settings():
    user, error = _browser_user()
    if error:
        return error
    csrf = _csrf_error()
    if csrf is not None:
        return csrf
    settings = validate_settings(request.get_json(silent=True) or {})
    user["screensaver"] = settings
    _main().update_user(user)
    return jsonify({"ok": True, "settings": settings})


@screensaver_bp.route("/playlist", methods=["GET"])
def playlist():
    user, error = _browser_user()
    if error:
        return error
    settings = load_settings(user)
    try:
        count = int(request.args.get("count", 12))
    except (TypeError, ValueError):
        count = 12
    try:
        offset = int(request.args.get("offset", 0))
    except (TypeError, ValueError):
        offset = 0
    if screensaver_visual.no_images(settings["sources"]):
        return jsonify({"items": [], "none": True})
    items, next_offset = build_playlist(user["id"], settings["sources"], count,
                                        order=settings["order"], offset=offset)
    if not items:
        return jsonify({
            "items": [],
            "error": "Keine Bilder gefunden. Quelle prüfen oder eigene Ordner in den Einstellungen füllen.",
        })
    return jsonify({"items": items, "next_offset": next_offset})


@screensaver_bp.route("/image/<image_id>", methods=["GET"])
def image(image_id):
    user, error = _browser_user()
    if error:
        return error
    if not _ID_RE.match(image_id or ""):
        return jsonify({"error": "Unbekanntes Bild"}), 404
    manifest = _read_json(_cache_dir() / f"{image_id}.json")
    if not manifest:
        return jsonify({"error": "Unbekanntes Bild"}), 404

    if manifest.get("kind") in ("local", "user", "journal"):
        path = _safe_local_path(manifest, user["id"])
        if path is None:
            return jsonify({"error": "Unbekanntes Bild"}), 404
        return send_file(path, max_age=3600)

    binary = _cache_dir() / f"{image_id}.bin"
    if not binary.is_file():
        url = manifest.get("url")
        if not isinstance(url, str) or not url.startswith("https://"):
            return jsonify({"error": "Unbekanntes Bild"}), 404
        try:
            if not _download(url, binary):
                return jsonify({"error": "Bild nicht ladbar"}), 502
        except Exception:
            return jsonify({"error": "Bild nicht ladbar"}), 502
    return send_file(binary, mimetype="image/jpeg", max_age=86400)


# ─── Private Ordner verwalten ────────────────────────────────────────
#
# Die Ordner liegen offen im Dateisystem, damit Sync und Freigaben sie ohne
# die App befuellen koennen. Jede Route loest Namen deshalb gegen die Wurzel
# des eigenen Kontos auf und verwirft alles, was dabei herausfuehrt.

_UNSAFE_CHARS = set('/\\:*?"<>|')


def _clean_name(value, limit=80):
    """Ordner- oder Dateiname ohne Pfadzeichen, Steuerzeichen und Randpunkte."""
    text = unicodedata.normalize("NFC", str(value or ""))
    text = "".join(char for char in text if char.isprintable() and char not in _UNSAFE_CHARS)
    return " ".join(text.split()).strip(" .")[:limit].strip(" .")


def _owned_folder(user_id, name):
    """Pfad eines bestehenden privaten Ordners oder ``None``."""
    if not isinstance(name, str) or not _FOLDER_RE.match(name):
        return None
    root = _user_collections_dir(user_id)
    folder = root / name
    try:
        if not folder.is_dir() or folder.is_symlink():
            return None
        folder.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    return folder


def _owned_file(folder, relative):
    if not isinstance(relative, str) or not relative:
        return None
    path = folder / relative
    try:
        resolved = path.resolve()
        resolved.relative_to(folder.resolve())
    except (OSError, ValueError):
        return None
    if path.is_symlink() or not resolved.is_file() or resolved.suffix.lower() not in MEDIA_SUFFIXES:
        return None
    return resolved


def _folder_summary(folder):
    images = _folder_images(folder)
    audio = _folder_audio(folder)
    try:
        size = sum(path.stat().st_size for path in images + audio)
    except OSError:
        size = 0
    return {"name": folder.name, "count": len(images), "audio": len(audio), "bytes": size}


def _rename_source(user, old, new=None):
    """Gewaehlte Quelle mitziehen (``new``) oder streichen (``None``) - in der
    aktuellen Einstellung, beim Player und in jedem Preset, das sie nennt."""
    def follow(sources):
        return [new if source == old else source for source in sources if new or source != old]

    changed = False
    settings = load_settings(user)
    if old in settings["sources"]:
        user["screensaver"] = validate_settings({**settings, "sources": follow(settings["sources"])})
        changed = True
    # Der Player kennt leere Quellen (dann schweigt er) - kein Rueckfall noetig.
    player = user.get("screensaver_player")
    if isinstance(player, dict) and old in (player.get("sources") or []):
        player["sources"] = follow(player["sources"])
        changed = True
    for preset in user.get("screensaver_presets") or []:
        if not isinstance(preset, dict):
            continue
        for area in ("screensaver", "player"):
            stored = preset.get(area)
            if isinstance(stored, dict) and old in (stored.get("sources") or []):
                # Leer bleibt leer: beim Laden springt dann die Voreinstellung ein.
                stored["sources"] = follow(stored["sources"])
                changed = True
    if changed:
        _main().update_user(user)


def _unique_target(folder, filename):
    stem, suffix = Path(filename).stem, Path(filename).suffix.lower()
    target = folder / f"{stem}{suffix}"
    number = 2
    while target.exists():
        target = folder / f"{stem} ({number}){suffix}"
        number += 1
    return target


def _store_upload(folder, upload):
    """Eine hochgeladene Datei im Original ablegen; liefert ``(name, fehler)``."""
    original = upload.filename or ""
    suffix = Path(original).suffix.lower()
    if suffix not in MEDIA_SUFFIXES:
        return original, "Kein unterstütztes Bild- oder Audioformat."
    limit = MAX_AUDIO_UPLOAD_BYTES if suffix in AUDIO_SUFFIXES else MAX_UPLOAD_BYTES
    stem = _clean_name(Path(original).stem, limit=120) or ("titel" if suffix in AUDIO_SUFFIXES else "bild")
    target = _unique_target(folder, f"{stem}{suffix}")
    temporary = folder / f".{target.name}.{uuid.uuid4().hex}.tmp"
    written = 0
    try:
        with temporary.open("wb") as handle:
            while True:
                chunk = upload.stream.read(UPLOAD_CHUNK)
                if not chunk:
                    break
                written += len(chunk)
                if written > limit:
                    return original, f"Größer als {limit // (1024 * 1024)} MB."
                handle.write(chunk)
            handle.flush()
            os.fsync(handle.fileno())
        if not written:
            return original, "Leere Datei."
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target.name, None


def _guarded_user():
    """Angemeldeter Nutzer plus CSRF-Pruefung fuer schreibende Routen."""
    user, error = _browser_user()
    if error:
        return None, error
    csrf = _csrf_error()
    if csrf is not None:
        return None, csrf
    return user, None


@screensaver_bp.route("/folders", methods=["GET"])
def list_folders():
    user, error = _browser_user()
    if error:
        return error
    return jsonify({
        "folders": [_folder_summary(folder) for folder in _folders(_user_collections_dir(user["id"]))],
        "max_upload_bytes": MAX_UPLOAD_BYTES,
        "max_audio_bytes": MAX_AUDIO_UPLOAD_BYTES,
    })


@screensaver_bp.route("/folders", methods=["POST"])
def create_folder():
    user, error = _guarded_user()
    if error:
        return error
    name = _clean_name((request.get_json(silent=True) or {}).get("name"))
    if not name or not _FOLDER_RE.match(name):
        return jsonify({"error": "Bitte einen gültigen Ordnernamen angeben."}), 400
    folder = _user_collections_dir(user["id"]) / name
    if folder.exists():
        return jsonify({"error": "Diesen Ordner gibt es schon."}), 409
    folder.mkdir(parents=True)
    return jsonify({"ok": True, "folder": _folder_summary(folder)}), 201


@screensaver_bp.route("/folders/<name>", methods=["GET"])
def folder_files(name):
    user, error = _browser_user()
    if error:
        return error
    folder = _owned_folder(user["id"], name)
    if folder is None:
        return jsonify({"error": "Ordner nicht gefunden."}), 404
    files = []
    for path in _folder_files(folder, MEDIA_SUFFIXES):
        relative = path.relative_to(folder).as_posix()
        try:
            size = path.stat().st_size
        except OSError:
            continue
        kind = "audio" if path.suffix.lower() in AUDIO_SUFFIXES else "image"
        files.append({"name": relative, "bytes": size, "kind": kind})
    return jsonify({"folder": _folder_summary(folder), "files": files})


@screensaver_bp.route("/folders/<name>", methods=["PATCH"])
def rename_folder(name):
    user, error = _guarded_user()
    if error:
        return error
    folder = _owned_folder(user["id"], name)
    if folder is None:
        return jsonify({"error": "Ordner nicht gefunden."}), 404
    new_name = _clean_name((request.get_json(silent=True) or {}).get("name"))
    if not new_name or not _FOLDER_RE.match(new_name):
        return jsonify({"error": "Bitte einen gültigen Ordnernamen angeben."}), 400
    target = folder.parent / new_name
    if new_name != name:
        if target.exists():
            return jsonify({"error": "Diesen Ordner gibt es schon."}), 409
        os.replace(folder, target)
        _rename_source(user, f"user:{name}", f"user:{new_name}")
    return jsonify({"ok": True, "folder": _folder_summary(target)})


@screensaver_bp.route("/folders/<name>", methods=["DELETE"])
def delete_folder(name):
    user, error = _guarded_user()
    if error:
        return error
    folder = _owned_folder(user["id"], name)
    if folder is None:
        return jsonify({"error": "Ordner nicht gefunden."}), 404
    removed = len(_folder_files(folder, MEDIA_SUFFIXES))
    shutil.rmtree(folder)
    _rename_source(user, f"user:{name}")
    return jsonify({"ok": True, "removed": removed})


@screensaver_bp.route("/folders/<name>/files", methods=["POST"])
def upload_files(name):
    user, error = _guarded_user()
    if error:
        return error
    folder = _owned_folder(user["id"], name)
    if folder is None:
        return jsonify({"error": "Ordner nicht gefunden."}), 404
    # Schon vor dem Einlesen abweisen: Werkzeug wuerde den Koerper sonst erst
    # vollstaendig zwischenspeichern. Der Browser schickt eine Datei je Anfrage;
    # die genaue Grenze je Typ prueft _store_upload beim Schreiben.
    if (request.content_length or 0) > MAX_AUDIO_UPLOAD_BYTES + UPLOAD_CHUNK:
        return jsonify({"error": f"Größer als {MAX_AUDIO_UPLOAD_BYTES // (1024 * 1024)} MB."}), 413
    uploads = request.files.getlist("files")
    if not uploads:
        return jsonify({"error": "Keine Datei erhalten."}), 400
    saved, rejected = [], []
    for upload in uploads:
        stored, problem = _store_upload(folder, upload)
        if problem:
            rejected.append({"name": stored, "error": problem})
        else:
            saved.append(stored)
    status = 200 if saved else 400
    return jsonify({"ok": bool(saved), "saved": saved, "rejected": rejected,
                    "folder": _folder_summary(folder)}), status


@screensaver_bp.route("/folders/<name>/files/<path:relative>", methods=["GET"])
def folder_file(name, relative):
    user, error = _browser_user()
    if error:
        return error
    folder = _owned_folder(user["id"], name)
    path = _owned_file(folder, relative) if folder else None
    if path is None:
        return jsonify({"error": "Datei nicht gefunden."}), 404
    return send_file(path, max_age=3600, conditional=True)


@screensaver_bp.route("/folders/<name>/files/<path:relative>", methods=["DELETE"])
def delete_file(name, relative):
    user, error = _guarded_user()
    if error:
        return error
    folder = _owned_folder(user["id"], name)
    path = _owned_file(folder, relative) if folder else None
    if path is None:
        return jsonify({"error": "Datei nicht gefunden."}), 404
    path.unlink()
    return jsonify({"ok": True, "folder": _folder_summary(folder)})
