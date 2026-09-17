"""Lossless, database-free Markdown parser used during the migration spike."""

import re
from collections import Counter
from dataclasses import dataclass
from enum import Enum

from .legacy_metadata import classify_legacy_metadata


_JOURNAL_HEADING_RE = re.compile(
    r"^## (?P<label>.+?) \| Datum & Uhrzeit: (?P<anchor>[^\n]+)\s*$", re.MULTILINE
)
_TAG_RE = re.compile(r"(?<![\w#])#(?P<tag>[\w-]+)", re.UNICODE)
_MEDIA_LINK_RE = re.compile(r"!?\[[^\]]*\]\((?P<path>[^)]+)\)")


class ParseStatus(str, Enum):
    CLEAN = "clean"
    LEGACY_METADATA = "legacy_metadata"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class JournalBlock:
    label: str
    anchor: str
    markdown: str


@dataclass(frozen=True)
class Document:
    source: str
    blocks: tuple
    tags: tuple
    media_links: tuple
    legacy_markers: tuple
    status: ParseStatus
    warnings: tuple


def normalize_newlines(content):
    """Normalize only line endings; all visible characters and final-newline state stay."""
    if not isinstance(content, str):
        raise TypeError("Markdown-Inhalt muss Text sein")
    return content.replace("\r\n", "\n").replace("\r", "\n")


def _journal_blocks(source):
    matches = list(_JOURNAL_HEADING_RE.finditer(source))
    blocks = []
    for index, match in enumerate(matches):
        next_heading = matches[index + 1].start() if index + 1 < len(matches) else len(source)
        next_separator = source.find("\n___", match.end(), next_heading)
        end = next_separator if next_separator >= 0 else next_heading
        blocks.append(JournalBlock(
            label=match.group("label"), anchor=match.group("anchor"),
            markdown=source[match.start():end],
        ))
    return tuple(blocks)


def parse_document(content):
    """Parse known safe structure while retaining the complete normalized document."""
    source = normalize_newlines(content)
    blocks = _journal_blocks(source)
    markers = classify_legacy_metadata(source)
    warnings = []
    anchors = Counter(block.anchor for block in blocks)
    duplicate_anchors = sorted(anchor for anchor, count in anchors.items() if count > 1)
    if duplicate_anchors:
        warnings.append("doppelte Zeitanker: " + ", ".join(duplicate_anchors))
    if any(not marker.valid for marker in markers):
        status = ParseStatus.BLOCKED
        warnings.append("unbekannte oder beschaedigte technische Metadaten")
    elif markers:
        status = ParseStatus.LEGACY_METADATA
    else:
        status = ParseStatus.CLEAN
    return Document(
        source=source,
        blocks=blocks,
        tags=tuple(sorted({match.group("tag") for match in _TAG_RE.finditer(source)}, key=str.casefold)),
        media_links=tuple(match.group("path") for match in _MEDIA_LINK_RE.finditer(source)),
        legacy_markers=markers,
        status=status,
        warnings=tuple(warnings),
    )
