"""Conservative readiness classification for legacy journal metadata.

This deliberately creates no cleaned Markdown.  A marker may only be removed in
the later apply phase after its complete semantic representation has been
persisted in PostgreSQL.  At present the schema has no approved target for the
legacy footer assignments, transcripts, or legacy agent/AI sessions.
"""

from collections import Counter
from dataclasses import dataclass
import json

from markdown import ParseStatus, parse_document, render_clean
from markdown.legacy_metadata import _FOOTER_RE, _MARKER_RE


@dataclass(frozen=True)
class LegacyMigrationDecision:
    status: str
    marker_kinds: tuple[str, ...]
    reasons: tuple[str, ...]
    visible_content_preserved: bool = True

    def report(self):
        return {
            "status": self.status,
            "marker_kinds": list(self.marker_kinds),
            "reasons": list(self.reasons),
            "visible_content_preserved": self.visible_content_preserved,
        }


def assess_legacy_migration(content):
    """Return a no-write decision; unknown and unfinished marker families block."""
    document = parse_document(content)
    markers = tuple(marker.kind for marker in document.legacy_markers)
    if document.status is ParseStatus.CLEAN:
        return LegacyMigrationDecision("ready", (), ())
    if document.status is ParseStatus.BLOCKED:
        return LegacyMigrationDecision("blocked", markers, tuple(document.warnings))
    return LegacyMigrationDecision(
        "ready_for_archive_cleanup", markers,
        ("Validierte Nutzlasten können vor der Bereinigung revisionsgebunden archiviert werden.",),
    )


def summary(decisions):
    """Aggregate report data only; callers retain paths separately."""
    status_counts = Counter(item.status for item in decisions)
    marker_counts = Counter(marker for item in decisions for marker in item.marker_kinds)
    return {"documents": len(decisions), "by_status": dict(sorted(status_counts.items())),
            "marker_kinds": dict(sorted(marker_counts.items()))}


def extract_legacy_metadata(content):
    """Extract valid metadata losslessly; reject anything not safe to remove."""
    decision = assess_legacy_migration(content)
    if decision.status == "blocked" and any("unbekannte oder beschaedigte" in reason for reason in decision.reasons):
        raise ValueError("Unbekannte oder beschädigte Legacy-Metadaten bleiben unverändert")
    records = []
    footer = _FOOTER_RE.search(content)
    if footer:
        records.append({"kind": "hashtag-index", "ordinal": 0,
                        "payload": json.loads(footer.group("payload"))})
    ordinal = Counter()
    for match in _MARKER_RE.finditer(content):
        kind = match.group("kind")
        if kind == "hashtag-index":
            continue
        ordinal[kind] += 1
        records.append({"kind": kind, "ordinal": ordinal[kind],
                        "payload": json.loads(match.group("payload").strip())})
    return tuple(records)


def render_without_legacy_metadata(content):
    """Remove only already validated technical comments; keep all visible text."""
    extract_legacy_metadata(content)
    rendered = _FOOTER_RE.sub("", content)
    rendered = _MARKER_RE.sub("", rendered)
    document = parse_document(rendered)
    return render_clean(document)


def archive_legacy_metadata(connection, *, source_revision_id, source_file_hash, records, now):
    """Persist immutable raw metadata before a later clean materialization."""
    from sqlalchemy import text

    inserted = 0
    for record in records:
        result = connection.execute(text("""
            INSERT INTO legacy_metadata_archive (
                source_revision_id, source_file_hash, marker_kind, ordinal, payload, extracted_at
            ) VALUES (:revision, :hash, :kind, :ordinal, CAST(:payload AS jsonb), :now)
            ON CONFLICT (source_revision_id, marker_kind, ordinal) DO NOTHING
        """), {"revision": source_revision_id, "hash": source_file_hash,
              "kind": record["kind"], "ordinal": record["ordinal"],
              "payload": json.dumps(record["payload"], ensure_ascii=False, sort_keys=True), "now": now})
        inserted += result.rowcount
    return inserted
