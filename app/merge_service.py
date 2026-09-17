"""Conservative Phase-6 three-way merge for unambiguous journal blocks."""

import hashlib
from dataclasses import dataclass

from markdown import parse_document


@dataclass(frozen=True)
class MergeResult:
    status: str
    content: str | None = None
    reason: str | None = None


def _hash(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _blocks(content):
    document = parse_document(content)
    if document.warnings or not document.blocks:
        raise ValueError("Dokumentstruktur ist nicht eindeutig")
    result = {}
    for block in document.blocks:
        if not block.anchor or block.anchor in result:
            raise ValueError("Blockanker ist nicht eindeutig")
        result[block.anchor] = block.markdown
    return document, result


def merge(base, external, jtracker):
    """Merge only independently changed uniquely anchored journal blocks.

    The output is deliberately suitable for a later DocumentStore operation, not
    a direct file write.  Any uncertain structure or same-block divergence is a
    conflict rather than a guessed merge.
    """
    try:
        base_doc, base_blocks = _blocks(base)
        external_doc, external_blocks = _blocks(external)
        jtracker_doc, jtracker_blocks = _blocks(jtracker)
    except ValueError as error:
        return MergeResult("conflict", reason=str(error))

    chosen = {}
    for anchor in set(base_blocks) | set(external_blocks) | set(jtracker_blocks):
        before = base_blocks.get(anchor)
        left = external_blocks.get(anchor)
        right = jtracker_blocks.get(anchor)
        if before is None:
            if left is None:
                chosen[anchor] = right
            elif right is None or _hash(left) == _hash(right):
                chosen[anchor] = left
            else:
                return MergeResult("conflict", reason="gleichzeitiger neuer Block")
            continue
        left_changed, right_changed = left != before, right != before
        if not left_changed:
            chosen[anchor] = right
        elif not right_changed:
            chosen[anchor] = left
        elif left == right:
            chosen[anchor] = left
        else:
            return MergeResult("conflict", reason="derselbe Block wurde unterschiedlich geaendert")

    if any(value is None for value in chosen.values()):
        # Both deletions are safe; deletion versus a modification was caught above.
        chosen = {anchor: value for anchor, value in chosen.items() if value is not None}
    base_order = [block.anchor for block in base_doc.blocks if block.anchor in chosen]
    new_order = sorted(set(chosen) - set(base_blocks))
    ordered = base_order + new_order
    prefix = base_doc.source[:base_doc.source.find(base_doc.blocks[0].markdown)]
    return MergeResult("merged", content=prefix + "\n___\n\n".join(chosen[anchor] for anchor in ordered))
