"""Tags und eingebettete Cover der Player-Titel (ID3, MP4, Vorbis, FLAC).

Gelesen wird mit ``mutagen``; fehlt es, bleibt es beim Titel aus dem
Dateinamen und dem ``cover.jpg`` im Ordner. Damit ein Neuaufbau des Titelindex
nicht jedes Mal alle Dateien oeffnet, merkt sich ein Cache je Wurzel die Tags
samt Groesse und Aenderungszeit unter ``DATA_DIR/cache/screensaver/audio-tags/``.
Eingebettete Cover werden erst beim ersten Abruf ausgepackt und unter
``audio-art/`` abgelegt. Beides ist jederzeit loeschbar.
"""

import base64

import screensaver

try:
    import mutagen
    from mutagen.flac import Picture
except ImportError:  # pragma: no cover - ohne mutagen nur Dateinamen
    mutagen = None
    Picture = None


TAG_FOLDER = "cache/screensaver/audio-tags"
ART_FOLDER = "cache/screensaver/audio-art"
TEXT_LIMIT = 160
ART_LIMIT = 10 * 1024 * 1024
ART_CACHE_LIMIT = 2000
IMAGE_TYPES = (
    (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
    (b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
    (b"GIF8", "image/gif", ".gif"),
)


def available():
    return mutagen is not None


def image_type(data):
    """``(mimetype, endung)`` fuer JPEG, PNG, GIF und WebP - sonst ``None``."""
    if not data:
        return None
    for magic, mimetype, suffix in IMAGE_TYPES:
        if data.startswith(magic):
            return mimetype, suffix
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", ".webp"
    return None


def _text(value):
    if isinstance(value, list):
        value = value[0] if value else ""
    return " ".join(str(value or "").split())[:TEXT_LIMIT]


def _embedded(audio):
    """Rohdaten des ersten eingebetteten Bildes oder ``None``."""
    pictures = getattr(audio, "pictures", None)  # FLAC
    if pictures:
        return pictures[0].data
    tags = audio.tags
    if tags is None:
        return None
    if hasattr(tags, "getall"):  # ID3 in MP3, AAC, WAV
        frames = tags.getall("APIC")
        return frames[0].data if frames else None
    covers = tags.get("covr") if hasattr(tags, "get") else None  # MP4
    if covers:
        return bytes(covers[0])
    blocks = tags.get("metadata_block_picture") if hasattr(tags, "get") else None  # Ogg
    if blocks and Picture is not None:
        try:
            return Picture(base64.b64decode(blocks[0])).data
        except Exception:
            return None
    return None


def read(path):
    """Titel, Interpret, Album und ob ein brauchbares Cover eingebettet ist."""
    if mutagen is None:
        return {}
    try:
        easy = mutagen.File(path, easy=True)
        full = mutagen.File(path)
    except Exception:  # kaputte oder exotische Datei: dann eben ohne Tags
        return {}
    tags = (easy.tags if easy is not None else None) or {}
    art = None
    if full is not None:
        try:
            art = _embedded(full)
        except Exception:
            art = None
    return {
        "title": _text(tags.get("title")),
        "artist": _text(tags.get("artist") or tags.get("albumartist")),
        "album": _text(tags.get("album")),
        "art": bool(art) and len(art) <= ART_LIMIT and image_type(art) is not None,
    }


def _cache_path(kind, owner):
    return screensaver._data_dir() / TAG_FOLDER / f"{screensaver._digest(f'{kind}:{owner}')}.json"


class TagCache:
    """Tags einer Wurzel; ``save`` behaelt nur, was beim Durchlauf gesehen wurde."""

    def __init__(self, kind, owner):
        self.path = _cache_path(kind, owner)
        stored = screensaver._read_json(self.path) if mutagen is not None else None
        self.old = stored.get("files", {}) if isinstance(stored, dict) else {}
        self.new = {}

    def get(self, path, relative):
        if mutagen is None:
            return {}
        try:
            stat = path.stat()
        except OSError:
            return {}
        stamp = [stat.st_mtime_ns, stat.st_size]
        cached = self.old.get(relative)
        if isinstance(cached, dict) and cached.get("stamp") == stamp:
            tags = cached
        else:
            tags = {**read(path), "stamp": stamp}
        self.new[relative] = tags
        return tags

    def save(self):
        if mutagen is None or self.new == self.old:
            return
        try:
            screensaver._write_json(self.path, {"files": self.new})
        except OSError:
            pass  # beim naechsten Neuaufbau wird eben noch einmal gelesen


def cover(path, key):
    """Eingebettetes Cover als ``(datei, mimetype)``, ausgepackt in den Cache."""
    if mutagen is None:
        return None
    try:
        stat = path.stat()
    except OSError:
        return None
    stem = screensaver._digest(f"{key}:{stat.st_mtime_ns}:{stat.st_size}")
    folder = screensaver._data_dir() / ART_FOLDER
    for _magic, mimetype, suffix in IMAGE_TYPES + ((b"", "image/webp", ".webp"),):
        cached = folder / f"{stem}{suffix}"
        if cached.is_file():
            return cached, mimetype
    try:
        audio = mutagen.File(path)
        data = _embedded(audio) if audio is not None else None
    except Exception:
        return None
    kind = image_type(data) if data and len(data) <= ART_LIMIT else None
    if kind is None:
        return None
    target = folder / f"{stem}{kind[1]}"
    try:
        screensaver._atomic_write(target, data)
    except OSError:
        return None
    prune(folder, ART_CACHE_LIMIT)
    return target, kind[0]


def prune(folder, limit):
    """Aelteste Bilder verwerfen, sobald ein Cache-Ordner ueber die Grenze waechst."""
    try:
        files = [path for path in folder.iterdir() if path.is_file() and not path.name.startswith(".")]
        if len(files) <= limit:
            return
        files.sort(key=lambda path: path.stat().st_mtime)
    except OSError:
        return
    for path in files[:len(files) - limit]:
        path.unlink(missing_ok=True)


__all__ = ["available", "read", "TagCache", "cover", "image_type", "prune"]
