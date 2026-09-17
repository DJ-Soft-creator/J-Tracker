"""Find and rewrite the transcript block belonging to one journal media marker.

Two layouts exist in real journals and both must stay readable.  A submitted
write session writes marker, heading and text separated by blank lines
(`write_sessions.media_markdown`), while the nightly worker used to insert the
three parts as adjacent lines.  Re-transcription therefore derives the extent of
a block from the surrounding structure instead of a fixed line count, and always
writes the current blank-line layout back.

The Markdown file stays the authoritative source; nothing here touches media
files, sidecars or indexes.
"""

import json
import re


HEADING_TAGS = "#Sprachnachricht #Transkription #Audio-Transkription"
HEADING = "### Transkript " + HEADING_TAGS

_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_HEADING_RE = re.compile(r"^#{1,6}\s+Transkript\b")
_ENTRY_SEPARATOR_RE = re.compile(r"^_{3,}$")
_AUDIO_MARKER_RE = re.compile(r"^<!--\s*jt:media\s+(\{.*\})\s*-->$")
# The label line a media marker is followed by, written by `media_markdown`.
_LABEL_RE = re.compile(r"^(?:Foto|Sprachnachricht|Dokument|Anhang)\b.*\bAufnahmezeit:")
# Anything that clearly belongs to the entry again rather than to the transcript.
_TEXT_END_RE = re.compile(r"^(?:<!--|~~.*~~$|_{3,}$|#{1,6}\s)")


def heading(title=None, position=None):
    """Return the transcript heading that names which recording it belongs to.

    With several voice messages in one entry a bare "Transkript" cannot be
    assigned to its recording any more.  A title given in the attachment strip
    wins; without one the position inside the entry is used, and a lone
    recording keeps the plain heading it always had.
    """
    label = " ".join(str(title or "").split())[:160]
    if label:
        return "### Transkript: %s %s" % (label, HEADING_TAGS)
    if position:
        return "### Transkript %d %s" % (int(position), HEADING_TAGS)
    return HEADING


def audio_positions(content):
    """Map every audio media id to its ``(position, count)`` inside its entry.

    Entries in a journal day are separated by ``___``; numbering restarts there
    so the second voice message of an entry is "Transkript 2" regardless of how
    many recordings the day already holds.
    """
    positions = {}
    for ids in _entry_blocks(content):
        for index, media_id in enumerate(ids, start=1):
            positions[media_id] = (index, len(ids))
    return positions


def _entry_blocks(content):
    """Yield the audio media ids of each entry, in the order they appear."""
    current = []
    for line in str(content or "").split("\n"):
        stripped = line.strip()
        if _ENTRY_SEPARATOR_RE.match(stripped):
            if current:
                yield current
            current = []
            continue
        match = _AUDIO_MARKER_RE.match(stripped)
        if not match:
            continue
        try:
            marker = json.loads(match.group(1))
        except ValueError:
            continue
        if isinstance(marker, dict) and marker.get("type") == "audio" and marker.get("id"):
            current.append(str(marker["id"]))
    if current:
        yield current


def transcript_marker(media_id):
    """Return the exact marker line the writers use for this media id."""
    if not _ID_RE.fullmatch(str(media_id or "")):
        raise ValueError("Ungültige Medien-ID")
    return '<!-- jt:transcript {"media_id":"%s"} -->' % media_id


def _ends_text(line):
    # In the packed layout the media label sits *behind* the transcript text, so
    # it terminates the block; swallowing it would delete it on the next run.
    stripped = line.strip()
    return not stripped or bool(_TEXT_END_RE.match(stripped)) or bool(_LABEL_RE.match(stripped))


def find_block(lines, media_id):
    """Return the ``[start, end)`` line range of an existing transcript block.

    ``end`` is exclusive.  A marker without a recognisable heading yields just
    the marker line, so a damaged block is repaired rather than duplicated.
    """
    needle = transcript_marker(media_id)
    for index, line in enumerate(lines):
        if line.strip() != needle:
            continue
        cursor = index + 1
        after_blank = cursor + 1 if cursor < len(lines) and not lines[cursor].strip() else cursor
        if after_blank < len(lines) and _HEADING_RE.match(lines[after_blank].strip()):
            cursor = after_blank + 1
            if cursor < len(lines) and not lines[cursor].strip():
                cursor += 1
            while cursor < len(lines) and not _ends_text(lines[cursor]):
                cursor += 1
        return index, cursor
    return None


def _insert_index(lines, media_id):
    """Return the line the transcript belongs after, i.e. behind the media label."""
    needle = '"id":"%s"' % media_id
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("<!-- jt:media") or needle not in stripped:
            continue
        cursor = index + 1
        after_blank = cursor + 1 if cursor < len(lines) and not lines[cursor].strip() else cursor
        if after_blank < len(lines) and _LABEL_RE.match(lines[after_blank].strip()):
            cursor = after_blank + 1
        return cursor
    return None


def upsert(content, media_id, text, title=None):
    """Replace the media's transcript, or insert one behind its media marker.

    Returns ``(content, changed)``.  Without a matching media marker the content
    is handed back untouched: a transcript must never float free of its media.
    ``title`` is the name the recording carries in the attachment strip; without
    one the heading falls back to the recording's position in its entry.
    """
    lines = str(content or "").split("\n")
    position, count = audio_positions(content).get(str(media_id), (None, 0))
    block = [
        transcript_marker(media_id), "",
        heading(title, position if count > 1 else None), "",
        str(text or "").strip(),
    ]
    existing = find_block(lines, media_id)
    if existing:
        start, end = existing
        if lines[start:end] == block:
            return content, False
        lines[start:end] = block
        return "\n".join(lines), True
    insert_at = _insert_index(lines, media_id)
    if insert_at is None:
        return content, False
    lines[insert_at:insert_at] = [""] + block
    return "\n".join(lines), True


def transcript_text(content, media_id):
    """Return the transcript currently written into the Markdown, if any."""
    lines = str(content or "").split("\n")
    existing = find_block(lines, media_id)
    if not existing:
        return None
    start, end = existing
    body = [line for line in lines[start + 1:end] if not _HEADING_RE.match(line.strip())]
    return "\n".join(body).strip()
