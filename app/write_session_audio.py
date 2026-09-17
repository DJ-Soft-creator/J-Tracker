"""Chunked voice recordings: assembling a take and rescuing interrupted ones.

Recordings arrive as numbered chunks and are only concatenated once the
recorder reports the take complete. A phone app killed mid-recording never
sends that report, so this module also finishes orphaned uploads on its own.
"""

import hashlib
import json
import mimetypes
import re
from datetime import datetime, timedelta, timezone

from scheduling import read_text_file, write_text_file


AUDIO_MIMES = {"audio/mp4": ".m4a", "audio/webm": ".webm", "audio/ogg": ".ogg", "audio/wav": ".wav", "audio/mpeg": ".mp3", "audio/aac": ".aac"}
MAX_AUDIO_CHUNK_BYTES = 8 * 1024 * 1024
# A recorder that has fallen silent this long is gone for good. Anything
# shorter would cut off a running recording, anything longer would keep the
# session blocked without reason.
UPLOAD_GRACE = timedelta(minutes=5)
_ID_RE = re.compile(r"^[0-9a-f-]{36}$")


def _now():
    return datetime.now(timezone.utc).astimezone()


def metadata(media_dir):
    path = media_dir / "metadata.json"
    if not path.is_file() or path.is_symlink():
        return None
    try:
        item = json.loads(read_text_file(path))
    except (OSError, json.JSONDecodeError):
        return None
    return item if isinstance(item, dict) else None


def _save(media_dir, item):
    write_text_file(media_dir / "metadata.json", json.dumps(item, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return item


def chunk_files(media_dir, count=None):
    """Return an upload's chunk files, contiguous from zero -- a gap ends the take."""
    chunks_dir = media_dir / "chunks"
    if not chunks_dir.is_dir():
        return []
    chunks = []
    while count is None or len(chunks) < count:
        chunk = chunks_dir / f"{len(chunks):06d}.part"
        if not chunk.is_file() or chunk.is_symlink():
            break
        chunks.append(chunk)
    return chunks


def assemble(media_dir, item, chunks):
    """Concatenate the chunks into the final audio file and mark it ready."""
    extension = AUDIO_MIMES.get(item["mime_type"], mimetypes.guess_extension(item["mime_type"]) or ".audio")
    original = media_dir / ("original" + extension)
    digest = hashlib.sha256()
    size = 0
    with original.open("wb") as output:
        for chunk in chunks:
            raw = chunk.read_bytes()
            output.write(raw)
            digest.update(raw)
            size += len(raw)
    item.update(
        status="ready", original_name=original.name, size=size, sha256=digest.hexdigest(),
        completed_at=_now().isoformat(timespec="seconds"), chunk_count=len(chunks),
    )
    return _save(media_dir, item)


def mark_failed(media_dir, item, reason):
    item.update(status="failed", upload_error=reason, updated_at=_now().isoformat(timespec="seconds"))
    return _save(media_dir, item)


def _last_activity(media_dir, item):
    """When the recorder last wrote here -- chunk mtimes outrank the metadata."""
    chunks_dir = media_dir / "chunks"
    stamps = [chunk.stat().st_mtime for chunk in chunks_dir.glob("*.part") if chunk.is_file()] if chunks_dir.is_dir() else []
    if stamps:
        return datetime.fromtimestamp(max(stamps), timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(item.get("received_at")))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def recover_stale(directory):
    """Finish uploads whose recorder never came back, so no session stays stuck.

    An attachment left in `uploading` blocks sending *and* discarding its
    session forever, which is exactly what a killed recording used to cause.
    Chunks that are contiguous from zero still play back, so they become a
    normal recording; anything else is marked failed and the UI offers to
    remove it.
    """
    media_root = directory / "media"
    if not media_root.is_dir():
        return False
    healed = False
    for media_dir in sorted(media_root.iterdir()):
        if not media_dir.is_dir() or media_dir.is_symlink() or not _ID_RE.fullmatch(media_dir.name):
            continue
        item = metadata(media_dir)
        if not item or item.get("type") != "audio" or item.get("status") != "uploading":
            continue
        activity = _last_activity(media_dir, item)
        if activity and _now() - activity < UPLOAD_GRACE:
            continue
        chunks = chunk_files(media_dir)
        if chunks:
            assemble(media_dir, item, chunks)
        else:
            mark_failed(media_dir, item, "Aufnahme abgebrochen, bevor Ton gesichert wurde")
        healed = True
    return healed
