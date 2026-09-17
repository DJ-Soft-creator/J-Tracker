"""Conservative classification of legacy J-Tracker Markdown metadata.

This module never removes content.  A later migration may only consume a marker after
it has made the corresponding database record in the same guarded operation.
"""

import json
import re
from dataclasses import dataclass


KNOWN_MARKERS = frozenset({
    "media", "transcript", "ai-session-config", "ai-session-event",
    "agent-session-config", "agent-session-event", "agent-session",
})
_MARKER_RE = re.compile(
    r"<!--\s*jt:(?P<kind>[a-z0-9_-]+)(?P<payload>.*?)-->", re.DOTALL
)
_FOOTER_RE = re.compile(
    r"<!-- jt:hashtag-index:start schema=\"1\" -->\s*"
    r"<!--\s*(?P<payload>.*?)\s*-->\s*"
    r"<!-- jt:hashtag-index:end -->\s*$", re.DOTALL
)


@dataclass(frozen=True)
class LegacyMarker:
    kind: str
    valid: bool
    reason: str = ""


def _valid_json_object(value):
    try:
        return isinstance(json.loads(value), dict)
    except (TypeError, ValueError):
        return False


def classify_legacy_metadata(content):
    """Return every known/unknown marker without making removal decisions."""
    markers = []
    footer = _FOOTER_RE.search(content)
    if footer:
        markers.append(LegacyMarker("hashtag-index", _valid_json_object(footer.group("payload"))))
    elif "jt:hashtag-index:" in content:
        markers.append(LegacyMarker("hashtag-index", False, "unvollstaendiger Footer"))

    for match in _MARKER_RE.finditer(content):
        kind = match.group("kind")
        if kind == "hashtag-index":
            # The footer (including its start marker) was handled above.
            continue
        if kind.startswith("hashtag-index-"):
            # Any other variant is not safe to guess.
            markers.append(LegacyMarker(kind, False, "unbekannte Hashtag-Markerform"))
        elif kind not in KNOWN_MARKERS:
            markers.append(LegacyMarker(kind, False, "unbekannter jt-Marker"))
        else:
            payload = match.group("payload").strip()
            markers.append(LegacyMarker(kind, _valid_json_object(payload), "ungueltiges JSON"))
    return tuple(markers)
