"""Bildschirmschoner: Vollflaechige Diashow als Schreib- und Diktierkulisse.

Drei Quellenarten, eine Mechanik:

* **Thema** — kuratierte Kategorien von Wikimedia Commons (``theme:<slug>``).
  Der Server holt die Bilder, nicht der Browser: die CSP bleibt bei
  ``img-src 'self'``, das Endgeraet spricht nie mit einem fremden Host und die
  Diashow laeuft auch offline aus dem Cache weiter.
* **Eigener Ordner** — jeder Unterordner unter ``DATA_DIR/screensaver/`` ist
  eine einzeln waehlbare Sammlung (``local:<ordner>``).
* **Journalbilder** — die eigenen Medien unter ``<user>/YYYY/MM/DD/media/``
  (``journal``).

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
import sys
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file


screensaver_bp = Blueprint("screensaver", __name__, url_prefix="/api/screensaver")

COLLECTIONS_FOLDER = "screensaver"
CACHE_FOLDER = "cache/screensaver"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
IMAGE_WIDTH = 1920
DOWNLOAD_TIMEOUT = 20
MAX_IMAGE_BYTES = 16 * 1024 * 1024
CATEGORY_TTL = 24 * 3600
LOCAL_INDEX_TTL = 600
CACHE_LIMIT = 400
PLAYLIST_MAX = 40
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".avif", ".gif"}
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
}
INFO_CHOICES = ("full", "clock", "image", "off")
TRANSITION_CHOICES = ("fade", "cut", "kenburns")
MODE_CHOICES = ("screensaver", "focus")
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
        # Ohne Quelle bliebe der Schoner schwarz; die Voreinstellung springt ein.
        settings["sources"] = list(dict.fromkeys(cleaned)) or list(DEFAULT_SETTINGS["sources"])

    for key, choices in (("info", INFO_CHOICES), ("transition", TRANSITION_CHOICES), ("mode", MODE_CHOICES)):
        if data.get(key) in choices:
            settings[key] = data[key]

    fade = data.get("fade_seconds")
    if isinstance(fade, (int, float)) and not isinstance(fade, bool):
        # Auf Zehntel gerastert, damit der Regler und der gespeicherte Wert
        # dieselbe Schrittweite haben.
        settings["fade_seconds"] = round(max(0.0, min(FADE_MAX, float(fade))) / FADE_STEP) * FADE_STEP
        settings["fade_seconds"] = round(settings["fade_seconds"], 1)

    dim = data.get("dim")
    if isinstance(dim, (int, float)) and not isinstance(dim, bool):
        settings["dim"] = max(0, min(80, int(dim)))
    return settings


def _known_source(source):
    if not isinstance(source, str):
        return False
    if source == "journal":
        return True
    if source.startswith("theme:"):
        return source[6:] in THEMES
    if source.startswith("local:"):
        return bool(_FOLDER_RE.match(source[6:]))
    return False


def load_settings(user):
    return validate_settings(user.get("screensaver"))


# ─── Quellen ─────────────────────────────────────────────────────────

def list_sources(user_id):
    """Alles, was sich als Quelle anwaehlen laesst — mit Bildanzahl, wo bekannt."""
    items = [
        {"id": f"theme:{slug}", "label": label, "kind": "theme"}
        for slug, (label, _categories) in THEMES.items()
    ]
    root = _collections_dir()
    if root.is_dir():
        for folder in sorted(root.iterdir(), key=lambda path: path.name.lower()):
            if not folder.is_dir() or folder.is_symlink() or folder.name.startswith("."):
                continue
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


def _folder_images(folder):
    try:
        return sorted(
            path for path in folder.rglob("*")
            if path.is_file() and not path.is_symlink() and path.suffix.lower() in IMAGE_SUFFIXES
        )
    except OSError:
        return []


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
            "title": title[:120],
            "author": field("Artist")[:80],
            "license": field("LicenseShortName")[:40],
            "link": info.get("descriptionurl") or "",
            "source": "Wikimedia Commons",
        })
    return items


# ─── Playlist ────────────────────────────────────────────────────────

def build_playlist(user_id, sources, count):
    """Gemischte Bildliste ueber alle gewaehlten Quellen.

    Fuer jedes Bild entsteht ein Manifest im Cache; die Antwort verweist nur
    auf dessen Kennung, nie auf einen Pfad oder eine fremde URL.
    """
    count = max(1, min(PLAYLIST_MAX, int(count)))
    selected = [source for source in sources if _known_source(source)] or list(DEFAULT_SETTINGS["sources"])
    per_source = max(2, -(-count // max(1, len(selected))) + 2)
    candidates = []
    for source in selected:
        if source == "journal":
            candidates.extend(_local_candidates(
                [(_user_root(user_id) / relative, relative) for relative in _journal_images(user_id)],
                per_source, "journal", str(user_id), "Mein Journal",
            ))
        elif source.startswith("local:"):
            folder = _collections_dir() / source[6:]
            if not folder.is_dir():
                continue
            files = [(path, str(path.relative_to(_collections_dir()))) for path in _folder_images(folder)]
            candidates.extend(_local_candidates(files, per_source, "local", "", folder.name))
        elif source.startswith("theme:"):
            candidates.extend(_theme_candidates(source[6:], per_source))

    random.shuffle(candidates)
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
    return items


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
    } for _absolute, relative in picked]


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
    if manifest.get("kind") == "journal":
        if str(manifest.get("owner")) != str(user_id):
            return None  # Journalbilder bleiben beim eigenen Konto.
        root = _user_root(user_id)
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
    items = build_playlist(user["id"], settings["sources"], count)
    if not items:
        return jsonify({
            "items": [],
            "error": "Keine Bilder gefunden. Quelle prüfen oder Ordner unter DATA_DIR/screensaver/ füllen.",
        })
    return jsonify({"items": items})


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

    if manifest.get("kind") in ("local", "journal"):
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
