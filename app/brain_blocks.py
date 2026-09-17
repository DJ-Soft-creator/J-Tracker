"""Resolving hashtag-index references and whole days to live Markdown blocks.

AI maintenance contract:
The index only ever points at blocks; visibility and content always come from
the current file, so a stale index can never widen what a caller sees. Imported
after brain_content.py because every lookup here resolves through
``_resolve_document``.
"""

import sys

_brain = sys.modules.get("brain")
if _brain is None:  # pragma: no cover - enforced by brain.py import order
    raise RuntimeError("brain facade must exist before domain modules are imported")
globals().update({name: getattr(_brain, name) for name in dir(_brain) if not name.startswith("__")})


def _sync_from_facade():
    for name in ("DATA_DIR", "FAMILY_DIR", "INDEX_INTERVAL_SECONDS", "enqueue_rebuild"):
        if hasattr(_brain, name):
            globals()[name] = getattr(_brain, name)


_INDEX_ANCHOR_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")


def _anchor_candidates(anchor):
    """Every stable_anchor spelling a document block can carry for one index anchor.

    The hashtag index normalises each block anchor to a full ISO timestamp, while
    a document block keeps the spelling of its own source line: a quick note has
    no year (``MM-DDTHH:MM:SS``) and a legacy quick note only a clock. Comparing
    the full form alone silently dropped every block that was not a
    "Datum & Uhrzeit" entry.
    """
    if not isinstance(anchor, str):
        return ()
    full = anchor.replace(" ", "T")
    candidates = [f"timestamp:{full}"]
    if _INDEX_ANCHOR_RE.fullmatch(full):
        candidates.append(f"timestamp:{full[5:]}")
        candidates.append(f"timestamp:{full[11:]}")
    return tuple(candidates)


def _tag_reference_block(user_id, reference):
    """Resolve an index reference to its current visible Markdown block."""
    source = reference.get("source")
    path = reference.get("path")
    if source not in {"personal", "family"} or not isinstance(path, str):
        return None, None
    document, _, _, _ = _resolve_document(user_id, f"{source}:{path}")
    if not document:
        return None, None
    if source == "personal":
        candidates = _anchor_candidates(reference.get("anchor"))
        block = next((
            item for item in document["blocks"] if item.get("stable_anchor") in candidates
        ), None) if candidates else None
    else:
        line = reference.get("start_line")
        block = next((
            item for item in document["blocks"]
            if isinstance(line, int) and item["start_line"] <= line <= item["end_line"]
        ), None)
    return document, block


def resolve_tag_block_references(user_id, tags, start=None, end=None, limit=None, sources=None):
    """Resolve hashtag-index references to live (document, block, reference) triples.

    The index only points at blocks; visibility and content always come from the
    current file, so a stale index can never widen access.  Family references
    anchor on a line number instead of a timestamp and therefore cannot answer a
    date-bounded question — they are dropped rather than returned out of range.
    """
    results = []
    seen = set()
    for reference in tagging_module.references_for_tags(user_id, tags, sources=sources):
        if (start or end) and (
            reference.get("source") != "personal"
            or not _in_date_range(reference.get("anchor"), start, end)
        ):
            continue
        document, block = _tag_reference_block(user_id, reference)
        if not block or block["fingerprint"] in seen:
            continue
        seen.add(block["fingerprint"])
        results.append((document, block, reference))
        if limit and len(results) >= limit:
            break
    return results


def journal_day_blocks(user_id, start, end, limit=None):
    """Return every visible block of the caller's journals in a date range.

    Deliberately not routed through the hashtag index: a day is asked for as a
    whole, including the blocks nobody ever tagged, and those are exactly the
    ones the index cannot name. Visibility still comes from the resolved
    document, so nothing becomes readable here that Brain would hide.
    """
    from datetime import timedelta

    blocks = []
    day = start
    while day <= end:
        relative = "{:%Y/%m/%d}/Journal_{:%Y-%m-%d}.md".format(day, day)
        document, _, _, _ = _resolve_document(user_id, "personal:" + relative)
        for block in (document or {}).get("blocks", []):
            blocks.append((document, block))
            if limit and len(blocks) >= limit:
                return blocks
        day += timedelta(days=1)
    return blocks


__all__ = [
    '_anchor_candidates', '_tag_reference_block', 'resolve_tag_block_references',
    'journal_day_blocks',
]
