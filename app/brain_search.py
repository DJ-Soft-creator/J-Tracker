"""Brain word search: query parsing, matching, ordering and tag facets.

AI maintenance contract: this module owns what the Brain search *means* - how a
query string becomes terms, what a block is matched against, how hits are
ordered and how the tag facets beside them are counted. It deliberately sits
between the tag filters and the content services so that a second backend
(PostgreSQL, once Phase 9.4 lands) can be slotted in behind the same functions
without the routes or the frontend noticing.

Two properties are load-bearing and easy to break:

* Matching is a plain substring over NFKD-normalised, diacritic-stripped,
  casefolded text - ``muller`` finds ``Müller`` and ``projek`` finds
  ``Projekte``. ``tests/test_brain_search_semantics.py`` nails that down.
* The haystack is precomputed by ``brain_documents.prepare_search_text`` once
  per index generation. Rebuilding it per request cost 246 ms for 2503 blocks.

Public compatibility names are re-exported by brain.py; import the facade from
application code.
"""

import sys

_brain = sys.modules.get("brain")
if _brain is None:  # pragma: no cover - enforced by brain.py import order
    raise RuntimeError("brain facade must exist before domain modules are imported")
globals().update({name: getattr(_brain, name) for name in dir(_brain) if not name.startswith("__")})


def _sync_from_facade():
    for name in ("DATA_DIR", "FAMILY_DIR"):
        if hasattr(_brain, name):
            globals()[name] = getattr(_brain, name)


_AI_TERM_PREFIXES = ("ki:", "ai:")


def _query_terms(query):
    """Split a query into plain terms and the ``ki:`` filters on machine tags.

    ``ki:`` alone asks for every block a model tagged, ``ki:urlaub`` for the
    blocks where that one hashtag came from the model rather than from the
    keyboard. Both negate with a leading minus, like any other term. Callers
    that know no machine origin (file lists) simply ignore the third value;
    dropping the terms here keeps ``ki:`` from being searched as literal text.
    """
    required = []
    excluded = []
    ai = {"required": [], "excluded": [], "any": False, "none": False}
    for quoted, plain in re.findall(r'"([^"]+)"|(\S+)', query or ""):
        term = _normalise(quoted or plain)
        if not term:
            continue
        negated = term.startswith("-") and len(term) > 1
        body = term[1:] if negated else term
        prefix = next((item for item in _AI_TERM_PREFIXES if body.startswith(item)), None)
        if prefix is not None:
            tag = body[len(prefix):].lstrip("#")
            if tag:
                ai["excluded" if negated else "required"].append(tag)
            else:
                ai["none" if negated else "any"] = True
        elif negated:
            excluded.append(body)
        else:
            required.append(body)
    return required, excluded, ai


def _matches_ai_terms(ai_tags, ai):
    """Check one item's machine-assigned hashtags against the ``ki:`` filters."""
    if not ai:
        return True
    machine = {_normalise(tag) for tag in ai_tags or []}
    if ai["any"] and not machine:
        return False
    if ai["none"] and machine:
        return False
    if any(tag not in machine for tag in ai["required"]):
        return False
    return not any(tag in machine for tag in ai["excluded"])


def _date_range():
    """Return the inclusive ISO date range requested by the Brain client."""
    raw_start = (request.args.get("start_date") or "").strip()
    raw_end = (request.args.get("end_date") or "").strip()
    try:
        start = date.fromisoformat(raw_start) if raw_start else None
        end = date.fromisoformat(raw_end) if raw_end else None
    except ValueError:
        return None, None, "Invalid ISO date range"
    if start and end and start > end:
        return None, None, "Start date must not be after end date"
    return start, end, None


def _in_date_range(value, start, end):
    try:
        current = date.fromisoformat((value or "")[:10])
    except ValueError:
        return False
    return (start is None or current >= start) and (end is None or current <= end)


def _matches_query(block, document, required, excluded, ai=None):
    """Match a block against the free-text query.

    The hashtags count as part of the text even when they are not in it: a
    machine tag and a tag carried over from an older footer live beside the
    block, and a plain search for the word has to find them all the same.
    """
    if required or excluded:
        # ``prepare_search_text`` has already normalised text, path and file
        # hashtags for this block; only the annotations of the signed-in user
        # are added here. Skeleton blocks from test fixtures carry no prepared
        # text and fall back to building it in place.
        haystack = search_haystack(block, block.get("manual_tags"))
        if haystack is None:
            haystack = _normalise("\n".join(
                [document["path"], block.get("text", "")]
                + [f"#{tag}" for tag in block.get("tags", [])]
            ))
        if not all(term in haystack for term in required):
            return False
        if any(term in haystack for term in excluded):
            return False
    return _matches_ai_terms(block.get("ai_tags", []), ai)


# Result cards carry a snippet, not the whole block: a search page with a
# handful of long journal entries would otherwise ship the entire day. The card
# says when it cut something so the UI can offer to fetch the rest.
SNIPPET_LIMIT = 360


def _serialise_result(document, block, user_id=None):
    text = block.get("text", "").strip()
    truncated = len(text) > SNIPPET_LIMIT
    snippet = text[:SNIPPET_LIMIT - 3].rstrip() + "..." if truncated else text
    result = {
        "doc_id": document["doc_id"],
        "source": document["source"],
        "source_label": {"personal": "Privat", "family": "Familie", "archive": "Archiv"}[document["source"]],
        "path": document["path"],
        "kind": document["kind"],
        "date": document["date"],
        "sort_at": block.get("sort_at", document["mtime"]),
        "fingerprint": block["fingerprint"],
        "start_line": block["start_line"],
        "snippet": snippet,
        "truncated": truncated,
        # A model's hashtags are not in the block text, so they have to be added
        # here to be visible at all; ``ai_tags`` lets the card mark which ones
        # they are instead of passing them off as the user's own.
        "tags": sorted(set(block.get("tags", [])) | set(block.get("ai_tags", []))),
        "ai_tags": block.get("ai_tags", []),
        "manual_tags": block.get("manual_tags", []),
        "priority": block.get("priority", "normal"),
        "project": block.get("project", ""),
        "read_only": document.get("read_only", False),
    }
    # Notes and projects can carry attachments too, and every personal source
    # resolves its media below the same private root as a journal.
    result["media"] = (
        write_sessions_module.media_from_text(user_id, text)
        if user_id and document.get("source") == "personal"
        else []
    )
    if document.get("source") == "family" and user_id:
        metadata = document.get("family_metadata") or {}
        result["management"] = {
            "can_manage": metadata.get("created_by") == user_id,
            "assigned_users": metadata.get("assigned_users") or [],
            "created_by": metadata.get("created_by") or "",
        }
    return result


def _match_sort_key(match):
    """Order matches exactly as ``_sort_results`` orders the serialised cards.

    Same three fields, read straight off the (document, block) pair, so that
    sorting before serialisation cannot change the order a page shows.
    """
    document, block = match
    return (block.get("sort_at", document["mtime"]), document["path"], block["start_line"])


def _sort_matches(matches, ordering):
    if ordering == "path":
        matches.sort(key=lambda match: (match[0]["path"], match[1]["start_line"]))
        return
    matches.sort(key=_match_sort_key, reverse=ordering != "oldest")


def _sort_results(results, ordering):
    if ordering == "oldest":
        results.sort(key=lambda item: (item["sort_at"], item["path"], item["start_line"]))
    elif ordering == "path":
        results.sort(key=lambda item: (item["path"], item["start_line"]))
    else:
        results.sort(key=lambda item: (item["sort_at"], item["path"], item["start_line"]), reverse=True)


# Tippfehlertoleranz. Die Schwelle ist die von PostgreSQL' pg_trgm: an echten
# Journalwörtern gemessen findet sie 254 von 300 künstlich vertippten Wörtern
# zurück und schlägt bei nur einer von fünf Unsinnsanfragen überhaupt etwas vor.
# Höher gewählt sinkt die Trefferquote spürbar, tiefer steigen die Fehlalarme.
SUGGESTION_THRESHOLD = 0.3
# Unter vier Zeichen ist ein Trigrammvergleich Raten: "haus" und "maus" teilen
# sich zu viel, und die Teilstringsuche findet kurze Bruchstücke ohnehin.
SUGGESTION_MIN_LENGTH = 4
_WORD_RE = re.compile(r"[0-9a-z_]{%d,}" % SUGGESTION_MIN_LENGTH)
_vocabulary_cache = {}
_vocabulary_lock = threading.Lock()


def _trigrams(word):
    padded = f"  {word} "
    return {padded[index:index + 3] for index in range(len(padded) - 2)}


def _build_vocabulary(documents):
    """Index every word of the visible blocks by trigram, for spelling repair.

    Built from the documents the caller may actually see, never from the shared
    index: suggesting a word that only occurs in a Family document out of reach
    would leak that the word exists at all.
    """
    postings = {}
    sizes = {}
    for document in documents:
        for block in document.get("blocks", []):
            for word in _WORD_RE.findall(block.get("search_base") or ""):
                if word in sizes:
                    continue
                grams = _trigrams(word)
                # Only the count is kept, not the set: scoring needs the size of
                # the union, and holding 22000 small sets cost 33 MB per account
                # against 6 MB this way - for a table that only a failed search
                # ever touches.
                sizes[word] = len(grams)
                for gram in grams:
                    postings.setdefault(gram, []).append(word)
    return postings, sizes


def _vocabulary(user_id, documents):
    """Return the trigram vocabulary, rebuilt only when a document changed.

    The key is derived from the documents themselves, so an edit, a new file or
    a different Family visibility all invalidate it without a separate signal.
    Building it costs about 220 ms, which is why it happens lazily: only a
    search that found nothing ever asks for it.
    """
    key = hash(tuple(
        (document.get("doc_id"), document.get("content_hash")) for document in documents
    ))
    with _vocabulary_lock:
        cached = _vocabulary_cache.get(user_id)
        if cached and cached[0] == key:
            return cached[1]
    vocabulary = _build_vocabulary(documents)
    with _vocabulary_lock:
        _vocabulary_cache[user_id] = (key, vocabulary)
    return vocabulary


def reset_search_caches():
    """Drop the per-account vocabulary. Only tests need this."""
    with _vocabulary_lock:
        _vocabulary_cache.clear()


def _closest_word(term, vocabulary):
    """The one word closest to a mistyped term, or "" if nothing is close."""
    postings, sizes = vocabulary
    grams = _trigrams(term)
    shared = {}
    for gram in grams:
        for word in postings.get(gram, ()):
            shared[word] = shared.get(word, 0) + 1
    best = ""
    best_score = ()
    for word, count in shared.items():
        similarity = count / (len(grams) + sizes[word] - count)
        if similarity < SUGGESTION_THRESHOLD:
            continue
        score = (similarity, -abs(len(word) - len(term)))
        if score > best_score:
            best, best_score = word, score
    return best


def _repaired_terms(user_id, documents, required):
    """Replace mistyped terms by their closest spelling in the user's own notes.

    Only reached when the query found nothing at all, so a successful search can
    never be diverted - and since matching is a substring test, "nothing at all"
    already proves the term occurs nowhere, not even inside a longer word. A
    half-typed prefix therefore never reaches this point.
    """
    candidates = [
        term for term in required
        if len(term) >= SUGGESTION_MIN_LENGTH and _WORD_RE.fullmatch(term)
    ]
    if not candidates:
        return required, []
    vocabulary = _vocabulary(user_id, documents)
    repaired = list(required)
    corrections = []
    for term in candidates:
        replacement = _closest_word(term, vocabulary)
        if not replacement or replacement == term:
            continue
        repaired[repaired.index(term)] = replacement
        corrections.append({"term": term, "replacement": replacement})
    return repaired, corrections


def _search_payload(documents, user_id, index_pending):
    required, excluded, ai = _query_terms((request.args.get("q") or "").strip())
    selected_tags = _selected_tags()
    excluded_tags = _excluded_tags()
    any_tags = _any_tags()
    start, end, date_error = _date_range()
    if date_error:
        return None, (jsonify({"error": date_error}), 400)
    kind = request.args.get("kind", "all")
    if kind not in {"all", "journal"}:
        return None, (jsonify({"error": "Invalid content kind"}), 400)
    catalog = tagging_module.catalog_view(user_id) if (selected_tags or excluded_tags or any_tags) else None
    resolve = _tag_resolver(catalog) if catalog else None
    ordering = request.args.get("order", "newest")
    if ordering not in {"newest", "oldest", "path"}:
        ordering = "newest"
    # Matches stay (document, block) pairs until the page is cut: serialising
    # every hit only to throw most of them away meant running media resolution
    # over the whole journal for a request that shows a hundred cards.
    def collect(terms):
        found = []
        for document in documents:
            if kind != "all" and document.get("kind") != kind:
                continue
            if not _in_date_range(document.get("date"), start, end):
                continue
            for block in document.get("blocks", []):
                if not _matches_query(block, document, terms, excluded, ai):
                    continue
                if selected_tags or excluded_tags or any_tags:
                    approved = _filterable_document_tags(
                        document, block.get("tags", []), catalog, resolve,
                    )
                    if not _tags_pass(approved, selected_tags, excluded_tags, any_tags):
                        continue
                found.append((document, block))
        return found

    matches = collect(required)
    corrections = []
    if not matches and required:
        repaired, corrections = _repaired_terms(user_id, documents, required)
        if corrections:
            matches = collect(repaired)
            if not matches:
                corrections = []
    _sort_matches(matches, ordering)
    total = len(matches)
    raw_limit = request.args.get("limit")
    raw_offset = request.args.get("offset", "0")
    try:
        offset = int(raw_offset)
        limit = int(raw_limit) if raw_limit is not None else total
    except ValueError:
        return None, (jsonify({"error": "Invalid result pagination"}), 400)
    if offset < 0 or limit < 0:
        return None, (jsonify({"error": "Invalid result pagination"}), 400)
    if raw_limit is not None:
        limit = min(limit, 200)
    page = [
        _serialise_result(document, block, user_id)
        for document, block in matches[offset:offset + limit]
    ]
    next_offset = offset + len(page)
    return {
        "results": page,
        # Empty unless the query found nothing and a spelling in the user's own
        # notes was close enough - the client says so instead of pretending the
        # hits belong to what was typed.
        "corrections": corrections,
        "order": ordering,
        "index_pending": index_pending,
        "total": total,
        "has_more": next_offset < total,
        "next_offset": next_offset,
    }, None


def _visible_tag_items(documents, catalog, selected_tags=None, excluded_tags=None,
                       block_scope=False, query_terms=None, any_tags=None):
    """Count the hashtags the current filter still leaves reachable.

    ``block_scope`` is what makes drilling down work in search: with a hashtag
    selected, only the blocks carrying it contribute their remaining tags — and
    the free-text query narrows them too — so the counts match the result list
    instead of dragging in every other tag of the same journal day. The file
    lists filter whole documents and therefore keep the document-wide count.

    ``any_tags`` is the theme group: it widens instead of narrowing, so the
    facets keep offering what occurs beside *any* of the chosen themes.

    ``ai_count`` says how many of a tag's hits a model assigned. Without it the
    machine hashtags are indistinguishable from the user's own proposals in a
    list of hundreds, which is the whole point of asking for them separately.
    """
    selected_tags = selected_tags or set()
    excluded_tags = excluded_tags or set()
    any_tags = any_tags or set()
    required, excluded, ai = query_terms or ([], [], None)
    counts = {}
    ai_counts = {}
    # One memo for the whole count: the same handful of hashtags is resolved
    # once per block otherwise, each time through two NFKD passes and two freshly
    # built catalog sets.
    resolve = _tag_resolver(catalog)
    for document in documents:
        for block in document.get("blocks", []):
            tags = _filterable_document_tags(document, block.get("tags", []), catalog, resolve)
            if block_scope:
                if not _tags_pass(tags, selected_tags, excluded_tags, any_tags):
                    continue
                if not _matches_query(block, document, required, excluded, ai):
                    continue
            machine = _filterable_document_tags(document, block.get("ai_tags", []), catalog, resolve)
            for tag in tags:
                counts[tag] = counts.get(tag, 0) + 1
                if tag in machine:
                    ai_counts[tag] = ai_counts.get(tag, 0) + 1
    family_canonical = set(catalog["family"].get("canonical", []))
    # Scope decides the chip colour, so a family proposal must count as family
    # even though only canonical family tags are offered without occurrences.
    family = family_canonical | set(catalog["family"].get("proposals", []))
    return [
        {
            "name": tag,
            "count": counts.get(tag, 0),
            "ai_count": ai_counts.get(tag, 0),
            "scope": "family" if tag in family else "personal",
        }
        for tag in sorted(family_canonical | set(counts))
    ]


__all__ = ['_trigrams', '_build_vocabulary', '_vocabulary', '_closest_word', '_repaired_terms', 'reset_search_caches', '_query_terms', '_matches_ai_terms', '_date_range', '_in_date_range', '_matches_query', 'SNIPPET_LIMIT', '_serialise_result', '_match_sort_key', '_sort_matches', '_sort_results', '_search_payload', '_visible_tag_items']
