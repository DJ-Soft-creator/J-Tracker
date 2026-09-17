"""Read-only validation of final journal-media markers for Phase 10.

The journal Markdown and the attachment files remain authoritative.  This module
only produces metadata suitable for an explicit later reference-table migration.
"""

import hashlib
import json
import re
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath


_MEDIA_MARKER_RE = re.compile(r"<!--\s*jt:media\s+(\{.*?\})\s*-->", re.DOTALL)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MEDIA_TYPES = frozenset({"image", "audio", "document"})


@dataclass(frozen=True)
class MediaReference:
    media_id: str | None
    media_path: str | None
    media_type: str | None
    mime_type: str | None
    sha256: str | None
    byte_size: int | None
    status: str
    reason: str | None = None

    def report(self):
        return asdict(self)


def _marker_path(root, value):
    if not isinstance(value, str) or not value:
        return None
    relative = PurePosixPath(value)
    if relative.is_absolute() or ".." in relative.parts or len(relative.parts) < 5:
        return None
    # Final attachments are deliberately confined to a dated journal media tree.
    year, month, day, media_directory = relative.parts[:4]
    if not (year.isdigit() and len(year) == 4 and month.isdigit() and len(month) == 2
            and day.isdigit() and len(day) == 2 and media_directory == "media"):
        return None
    candidate = (root / Path(*relative.parts)).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def _file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_media_references(root, markdown_content):
    """Return only verified metadata or explicit reasons why a marker is unsafe.

    Invalid JSON, duplicate IDs and missing/symlinked/hash-mismatched files are
    reported rather than guessed.  No filesystem or database state is changed.
    """
    root = Path(root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Medienroot muss ein Verzeichnis sein")
    if not isinstance(markdown_content, str):
        raise TypeError("Markdown-Inhalt muss Text sein")
    results, seen_ids = [], set()
    for raw in _MEDIA_MARKER_RE.findall(markdown_content):
        try:
            marker = json.loads(raw)
        except json.JSONDecodeError:
            results.append(MediaReference(None, None, None, None, None, None, "invalid", "ungueltiges Marker-JSON"))
            continue
        if not isinstance(marker, dict):
            results.append(MediaReference(None, None, None, None, None, None, "invalid", "Marker ist kein Objekt"))
            continue
        media_id, media_path = marker.get("id"), marker.get("media_path")
        media_type, mime_type, expected_hash = marker.get("type"), marker.get("mime_type"), marker.get("sha256")
        try:
            canonical_id = str(uuid.UUID(str(media_id)))
        except (ValueError, TypeError, AttributeError):
            results.append(MediaReference(None, media_path, media_type, mime_type, expected_hash, None, "invalid", "ungueltige Medien-ID"))
            continue
        if canonical_id in seen_ids:
            results.append(MediaReference(canonical_id, media_path, media_type, mime_type, expected_hash, None, "invalid", "doppelte Medien-ID"))
            continue
        seen_ids.add(canonical_id)
        if media_type not in _MEDIA_TYPES or not isinstance(mime_type, str) or not mime_type:
            results.append(MediaReference(canonical_id, media_path, media_type, mime_type, expected_hash, None, "invalid", "ungueltiger Medientyp"))
            continue
        if media_path is None and expected_hash is None:
            results.append(MediaReference(canonical_id, None, media_type, mime_type, None, None,
                                          "legacy_unresolved", "Legacy-Marker ohne Dateipfad und SHA-256"))
            continue
        if not isinstance(expected_hash, str) or not _SHA256_RE.fullmatch(expected_hash):
            results.append(MediaReference(canonical_id, media_path, media_type, mime_type, expected_hash, None, "invalid", "ungueltiger SHA-256"))
            continue
        path = _marker_path(root, media_path)
        if path is None:
            results.append(MediaReference(canonical_id, media_path, media_type, mime_type, expected_hash, None, "invalid", "Medienpfad nicht erlaubt"))
            continue
        if path.is_symlink():
            results.append(MediaReference(canonical_id, media_path, media_type, mime_type, expected_hash, None, "invalid", "Medienpfad ist Symlink"))
            continue
        if not path.is_file():
            results.append(MediaReference(canonical_id, media_path, media_type, mime_type, expected_hash, None, "missing", "Mediendatei fehlt"))
            continue
        actual_hash = _file_sha256(path)
        if actual_hash != expected_hash:
            results.append(MediaReference(canonical_id, media_path, media_type, mime_type, expected_hash, path.stat().st_size, "hash_mismatch", "Dateihash weicht ab"))
            continue
        results.append(MediaReference(canonical_id, media_path, media_type, mime_type, expected_hash, path.stat().st_size, "verified"))
    return tuple(results)


def report_media_references(references):
    """Return aggregate-only JSON-safe inventory data without Markdown content."""
    counts = {}
    for reference in references:
        counts[reference.status] = counts.get(reference.status, 0) + 1
    return {"summary": {"references": len(references), "by_status": counts},
            "references": [reference.report() for reference in references]}


def verify_persisted_reference(root, *, media_id, media_path, media_type, mime_type, file_hash, byte_size):
    """Re-check one stored reference against the authoritative attachment file."""
    path = _marker_path(Path(root).resolve(strict=True), media_path)
    if path is None:
        return MediaReference(str(media_id), media_path, media_type, mime_type, file_hash, byte_size,
                              "invalid", "gespeicherter Medienpfad nicht erlaubt")
    if path.is_symlink():
        return MediaReference(str(media_id), media_path, media_type, mime_type, file_hash, byte_size,
                              "invalid", "gespeicherter Medienpfad ist Symlink")
    if not path.is_file():
        return MediaReference(str(media_id), media_path, media_type, mime_type, file_hash, byte_size,
                              "missing", "referenzierte Mediendatei fehlt")
    actual_size = path.stat().st_size
    if actual_size != byte_size:
        return MediaReference(str(media_id), media_path, media_type, mime_type, file_hash, actual_size,
                              "size_mismatch", "Dateigroesse weicht ab")
    if _file_sha256(path) != file_hash:
        return MediaReference(str(media_id), media_path, media_type, mime_type, file_hash, actual_size,
                              "hash_mismatch", "Dateihash weicht ab")
    return MediaReference(str(media_id), media_path, media_type, mime_type, file_hash, actual_size, "verified")
