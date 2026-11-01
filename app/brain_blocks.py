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


def _footer_anchors_for_block(document, block, footer, journal_date):
    """The footer anchors whose machine hashtags this card shows.

    Mirrors how ``_machine_tag_lookup`` attached them in the first place: every
    spelling of the footer anchor, plus the whole-day anchor of an old journal,
    which lands on the first block when no block is stamped with it.
    """
    anchors = [
        anchor for anchor in tagging_module.footer_ai_tags(footer)
        if block.get("stable_anchor") in _anchor_candidates(anchor)
    ]
    blocks = document.get("blocks", [])
    day_anchor = f"{journal_date} 00:00:00" if journal_date else ""
    if day_anchor and blocks and blocks[0]["fingerprint"] == block["fingerprint"]:
        stamped = {item.get("stable_anchor") for item in blocks}
        if not stamped & set(_anchor_candidates(day_anchor)) and day_anchor not in anchors:
            anchors.append(day_anchor)
    return anchors


def reject_block_ai_tags(user_id, doc_id, fingerprint, tags):
    """Take machine hashtags back from one journal block and keep them out.

    The footer is the only place these hashtags exist, so it is rewritten
    through the tagging module: the tags leave the block's entry and the ``ai``
    map and land in ``ai_rejected``, which the next tagging run respects.
    Returns ``(status, removed)`` with status ``ok``, ``not_found`` or
    ``conflict`` (the block no longer resolves).
    """
    document, path, root, _ = _resolve_document(user_id, doc_id)
    if not document or document["source"] != "personal" or document["kind"] != "journal" or document["read_only"]:
        return "not_found", []
    block = next((item for item in document["blocks"] if item["fingerprint"] == fingerprint), None)
    if not block:
        return "conflict", []
    journal_date = tagging_module.journal_date_from_path(path)
    with _index_guard("personal", user_id, exclusive=True):
        def update(current):
            body, footer = tagging_module.strip_footer(current)
            if not footer:
                return current, []
            current_document = _build_document("personal", root, path, "journal", current) or document
            current_block = next(
                (item for item in current_document["blocks"] if item["fingerprint"] == fingerprint), None,
            )
            if not current_block:
                return current, None
            anchors = _footer_anchors_for_block(current_document, current_block, footer, journal_date)
            changed, removed = tagging_module.reject_ai_tags(footer, anchors, tags)
            if not removed:
                return current, []
            return tagging_module.refresh_journal_footer(
                user_id, body, journal_date, existing_footer=changed,
            ), removed

        removed = update_text_file(path, update)
        if removed is None:
            return "conflict", []
        if removed:
            updated = _build_document("personal", root, path, "journal")
            if updated:
                _write_index_document("personal", updated, user_id)
    if removed:
        enqueue_rebuild(user_id)
    return "ok", removed


__all__ = [
    '_anchor_candidates', '_tag_reference_block', 'resolve_tag_block_references',
    'journal_day_blocks', 'reject_block_ai_tags',
]
