"""Brain tag-chip include/exclude filters for search, facets and file lists.

AI maintenance contract:
Own selected/excluded/theme tag parsing and the matching predicates used by
search, tag facets and personal/family file lists. Rendering and HTTP orchestration
belong in the frontend and brain_routes.py.
"""

import sys

_brain = sys.modules.get("brain")
if _brain is None:  # pragma: no cover - enforced by brain.py import order
    raise RuntimeError("brain facade must exist before domain modules are imported")
globals().update({name: getattr(_brain, name) for name in dir(_brain) if not name.startswith("__")})


def _sync_from_facade():
    pass


def _selected_tags():
    tags = []
    for value in request.args.getlist("tags"):
        tags.extend(value.split(","))
    return {_normalise(tag.strip()) for tag in tags if tag.strip()}


def _excluded_tags():
    tags = []
    for value in request.args.getlist("exclude_tags"):
        tags.extend(value.split(","))
    return {_normalise(tag.strip()) for tag in tags if tag.strip()}


def _any_tags():
    """The "weitere Themen" group: one hit in it is enough (OR).

    The selected tags narrow (AND) and therefore only ever offer hashtags that
    already occur beside the current ones. A second, unrelated topic can only
    be added by widening, so it travels in its own set.
    """
    tags = []
    for value in request.args.getlist("any_tags"):
        tags.extend(value.split(","))
    return {_normalise(tag.strip()) for tag in tags if tag.strip()}


def _tags_pass(tags, selected_tags, excluded_tags, any_tags=None):
    """Whether a block or document carrying ``tags`` survives the tag filter.

    Reading order: an excluded hashtag wins over everything, the theme group is
    an OR gate, and the selected hashtags refine what got through it with AND.
    """
    if excluded_tags and excluded_tags & tags:
        return False
    if any_tags and not (any_tags & tags):
        return False
    return not selected_tags or selected_tags.issubset(tags)


def _catalogs_for(document, catalog):
    """Which catalogs a document's hashtags are resolved against."""
    if document.get("source") == "family":
        return [catalog["family"]]
    return [catalog["personal"], catalog["family"]]


def _tag_resolver(catalog):
    """Return a memoised ``(document, tags) -> filterable tags`` for one request.

    ``tagging_module.filterable_tag`` normalises a tag with an NFKD character
    loop, does it a second time inside ``canonical_tag``, and rebuilds the
    canonical and proposal sets on every single call. Counting the facets of a
    whole index repeats that thousands of times for the same handful of answers
    - measured at 52 to 99 ms per request. The catalogs cannot change while a
    request runs, so one memo per request is both safe and enough.
    """
    memo = {}

    def resolve(document, tags):
        scope = "family" if document.get("source") == "family" else "personal"
        found = set()
        for tag in tags:
            key = (scope, tag)
            if key not in memo:
                memo[key] = tagging_module.filterable_tag(tag, _catalogs_for(document, catalog))
            if memo[key]:
                found.add(memo[key])
        return found

    return resolve


def _filterable_document_tags(document, tags, catalog, resolve=None):
    """Tags of this document that may take part in filters and facets.

    Includes proposals, not just canonical tags: a hashtag has to be findable
    before one decides whether to approve it (``tagging_module.filterable_tag``).

    ``resolve`` is an optional ``_tag_resolver`` shared across one request; every
    caller that runs this over many blocks should pass one.
    """
    if resolve is not None:
        return resolve(document, tags)
    return {
        canonical for tag in tags
        if (canonical := tagging_module.filterable_tag(tag, _catalogs_for(document, catalog)))
    }


def _documents_with_selected_tags(documents, catalog, selected_tags, excluded_tags=None, any_tags=None):
    """Keep only documents that survive the tag filter (see ``_tags_pass``).

    Tag chips are document facets: selecting/excluding a tag narrows the chips
    to the remaining files, rather than continuing to offer unrelated tags.
    """
    excluded_tags = excluded_tags or set()
    any_tags = any_tags or set()
    if not selected_tags and not excluded_tags and not any_tags:
        return documents
    matching = []
    resolve = _tag_resolver(catalog)
    for document in documents:
        tags = {
            tag
            for block in document.get("blocks", [])
            for tag in block.get("tags", [])
        }
        approved = _filterable_document_tags(document, tags, catalog, resolve)
        if not _tags_pass(approved, selected_tags, excluded_tags, any_tags):
            continue
        matching.append(document)
    return matching


def _document_tag_paths(user_id, source, selected_tags, excluded_tags=None, any_tags=None):
    """Resolve the current documents matching the tag filter once per list."""
    excluded_tags = excluded_tags or set()
    any_tags = any_tags or set()
    if not selected_tags and not excluded_tags and not any_tags:
        return None
    documents, _ = _visible_documents(user_id)
    catalog = tagging_module.catalog_view(user_id)
    matching = set()
    for document in documents:
        if document["source"] != source:
            continue
        tags = {
            tag for block in document.get("blocks", [])
            for tag in block.get("tags", [])
        }
        approved = _filterable_document_tags(document, tags, catalog)
        if not _tags_pass(approved, selected_tags, excluded_tags, any_tags):
            continue
        matching.add(document["path"])
    return matching


__all__ = [
    '_selected_tags', '_excluded_tags', '_any_tags', '_tags_pass', '_catalogs_for', '_tag_resolver', '_filterable_document_tags',
    '_documents_with_selected_tags', '_document_tag_paths',
]
