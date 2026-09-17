"""Confirmed personal-document full-text index for Phase 9."""

from datetime import date, timedelta, timezone


def index_document(connection, *, document_id, revision_id, content, now):
    """Replace the searchable confirmed state of one document in the same transaction."""
    from sqlalchemy import text

    owner = connection.scalar(text("SELECT owner_id FROM documents WHERE id = :id"), {"id": document_id})
    if owner is None:
        raise LookupError("Dokument nicht gefunden")
    connection.execute(text("""
        INSERT INTO document_search (document_id, owner_id, revision_id, search_text, search_vector, indexed_at)
        VALUES (:document, :owner, :revision, :content, to_tsvector('german', :content), :now)
        ON CONFLICT (document_id) DO UPDATE SET owner_id = EXCLUDED.owner_id,
            revision_id = EXCLUDED.revision_id, search_text = EXCLUDED.search_text,
            search_vector = EXCLUDED.search_vector, indexed_at = EXCLUDED.indexed_at
    """), {"document": document_id, "owner": owner, "revision": revision_id,
            "content": content, "now": now.astimezone(timezone.utc)})


def parse_date_range(start_date, end_date):
    """Translate an inclusive ISO date range into relative_path prefix bounds.

    Managed journals live at ``YYYY/MM/DD/Journal_YYYY-MM-DD.md``, which sorts
    lexicographically by date.  A range filter therefore needs no extra column.
    """
    try:
        start = date.fromisoformat(start_date) if start_date else None
        end = date.fromisoformat(end_date) if end_date else None
    except (TypeError, ValueError):
        return None, None, "Ungültiges ISO-Datum"
    if start and end and start > end:
        return None, None, "Startdatum darf nicht nach dem Enddatum liegen"
    path_from = "{:%Y/%m/%d}/".format(start) if start else None
    path_to = None
    if end and end < date.max:
        path_to = "{:%Y/%m/%d}/".format(end + timedelta(days=1))
    return path_from, path_to, None


_ORDERINGS = {
    "newest": "document.relative_path DESC, rank DESC",
    "oldest": "document.relative_path ASC, rank DESC",
    "path": "document.relative_path ASC",
}
_RELEVANCE_ORDER = "rank DESC, document.relative_path DESC"


def search_personal(connection, *, owner_id, query, limit=50, offset=0,
                    path_from=None, path_to=None, paths=None, order=None):
    """Return one page of the current user's confirmed managed journal hits.

    ``paths`` restricts the result to an explicit relative_path allowlist and is
    how hashtag filtering is applied: tags stay owned by the rebuildable file
    index, so they never need a column in this table.
    """
    from sqlalchemy import bindparam, text

    if not isinstance(query, str) or not query.strip():
        return {"results": [], "total": 0}
    if not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("Limit muss zwischen 1 und 100 liegen")
    if not isinstance(offset, int) or offset < 0:
        raise ValueError("Offset darf nicht negativ sein")
    if paths is not None and not paths:
        return {"results": [], "total": 0}

    conditions = [
        "search.owner_id = :owner", "document.scope = 'personal'",
        "document.document_type = 'journal'", "document.storage_mode = 'db_managed'",
        "document.state = 'active'",
        "search.search_vector @@ websearch_to_tsquery('german', :query)",
    ]
    params = {"owner": owner_id, "query": query.strip(), "limit": limit, "offset": offset}
    if path_from:
        conditions.append("document.relative_path >= :path_from")
        params["path_from"] = path_from
    if path_to:
        conditions.append("document.relative_path < :path_to")
        params["path_to"] = path_to
    if paths is not None:
        conditions.append("document.relative_path IN :paths")
        params["paths"] = list(paths)
    where = " AND ".join(conditions)

    def prepared(sql):
        statement = text(sql)
        return statement.bindparams(bindparam("paths", expanding=True)) if paths is not None else statement

    total = connection.scalar(prepared("""
        SELECT COUNT(*) FROM document_search search
        JOIN documents document ON document.id = search.document_id
        WHERE """ + where), params)
    rows = connection.execute(prepared("""
        SELECT document.relative_path, search.revision_id,
               ts_headline('german', search.search_text, websearch_to_tsquery('german', :query),
                   'StartSel=<mark>, StopSel=</mark>, MaxWords=20, MinWords=8, MaxFragments=2') AS excerpt,
               ts_rank_cd(search.search_vector, websearch_to_tsquery('german', :query)) AS rank
        FROM document_search search JOIN documents document ON document.id = search.document_id
        WHERE """ + where + """
        ORDER BY """ + _ORDERINGS.get(order, _RELEVANCE_ORDER) + """
        LIMIT :limit OFFSET :offset
    """), params).mappings().all()
    return {"results": rows, "total": int(total or 0)}
