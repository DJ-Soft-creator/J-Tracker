"""Tool implementations behind the MCP endpoint.

AI maintenance contract: this module holds tool logic only. JSON-RPC framing,
protocol versions and bearer authentication belong to mcp_server.py.

No tool owns a search or visibility rule of its own: full text comes from
db_search.py (the same Phase 9 index the Brain journal tab queries), hashtag
blocks come from brain.resolve_tag_block_references and whole days from
brain.journal_day_blocks — both re-read the source Markdown, so a stale index can
never widen what a caller sees.

Every runner takes ``sources``, which the endpoint sets to its own area. It is
never a caller argument.
"""

import sys

_core = sys.modules.get("main") or sys.modules.get("__main__")
if _core is None:  # pragma: no cover - import order is a hard application invariant
    raise RuntimeError("main facade must exist before mcp_tools is imported")
globals().update({name: getattr(_core, name) for name in dir(_core) if not name.startswith("__")})
_DYNAMIC_NAMES = ('DATA_DIR', 'IS_DEV', 'database_url', 'is_enabled')

import brain as brain_module
import tagging as tagging_module

MAX_SEARCH_LIMIT = 100
MAX_BLOCK_LIMIT = 200
MAX_BLOCK_CHARS = 4000
MAX_DAY_LIMIT = 366
MAX_DAY_SPAN = 7

from datetime import date as _DATE_TYPE


class ToolInputError(ValueError):
    """Caller supplied arguments the model can correct and retry."""


class ToolUnavailable(RuntimeError):
    """The tool cannot run in this deployment at all."""


def _sync_runtime():
    for name in _DYNAMIC_NAMES:
        if hasattr(_core, name):
            globals()[name] = getattr(_core, name)


def _bounded(value, default, maximum):
    if value is None:
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ToolInputError("limit muss eine ganze Zahl sein") from None
    return max(1, min(number, maximum))


def _tag_list(tags):
    if isinstance(tags, str):
        tags = [tags]
    if not isinstance(tags, list) or not tags:
        raise ToolInputError("tags muss eine nicht-leere Liste von Hashtags sein")
    cleaned = [str(tag).strip().lstrip("#") for tag in tags]
    cleaned = [tag for tag in cleaned if tag]
    if not cleaned:
        raise ToolInputError("tags enthält keinen verwertbaren Hashtag")
    return cleaned


def search_available():
    """Full-text search needs the Phase 9 DB index, which is DEV-gated."""
    _sync_runtime()
    return bool(IS_DEV and is_enabled())


def run_search(user_id, *, query, sources=None, tags=None, start_date=None, end_date=None, limit=None, offset=0):
    """Rank the caller's confirmed managed journals for a full-text query."""
    _sync_runtime()
    if not search_available():
        raise ToolUnavailable("Die Journal-Volltextsuche ist auf diesem Server nicht aktiv.")
    if sources and "personal" not in sources:
        # The full-text index only holds personal journals, so a family-only
        # endpoint has nothing to rank. It never offers this tool either.
        raise ToolUnavailable("Die Volltextsuche steht nur im privaten Bereich zur Verfügung.")
    if not isinstance(query, str) or not query.strip():
        raise ToolInputError("query darf nicht leer sein")
    from db_search import parse_date_range, search_personal

    path_from, path_to, range_error = parse_date_range(start_date, end_date)
    if range_error:
        raise ToolInputError(range_error)
    paths = None
    if tags:
        paths = _core._journal_search_tag_paths(user_id, _tag_list(tags))
    try:
        offset = max(0, int(offset or 0))
    except (TypeError, ValueError):
        raise ToolInputError("offset muss eine ganze Zahl sein") from None

    from sqlalchemy import create_engine

    engine = create_engine(database_url(), pool_pre_ping=True, pool_size=2, max_overflow=0)
    try:
        with engine.connect() as connection:
            found = search_personal(
                connection, owner_id=user_id, query=query,
                limit=_bounded(limit, 30, MAX_SEARCH_LIMIT), offset=offset,
                path_from=path_from, path_to=path_to, paths=paths,
            )
    except ValueError as error:
        raise ToolInputError(str(error)) from error
    finally:
        engine.dispose()
    return {
        "results": [
            {"path": row["relative_path"], "excerpt": row["excerpt"], "rank": float(row["rank"])}
            for row in found["results"]
        ],
        "total": found["total"],
        "next_offset": offset + len(found["results"]),
    }


def run_list_tags(user_id, sources=None):
    """List every hashtag the caller can filter on, with its block count.

    Proposed tags count as much as approved ones: the model has to see a fresh
    hashtag to be able to ask for it, and approval only curates the catalog.
    Blocked tags are absent because they never reach the hashtag index either.

    ``sources`` follows the endpoint: a family-only server lists the family
    catalog alone, so the private vocabulary never leaks into it.
    """
    _sync_runtime()
    allowed = set(sources) if sources else {"personal", "family"}
    personal = tagging_module.read_catalog(user_id)
    family = tagging_module.read_catalog(family=True)
    counts = {}
    for reference in tagging_module.references_for_tags(user_id, [], sources=sources):
        for tag in reference.get("tags", []):
            counts[tag] = counts.get(tag, 0) + 1
    scopes = {}
    for scope, catalog in (("family", family), ("personal", personal)):
        if scope not in allowed:
            continue
        for tag in set(catalog.get("canonical", [])) | set(catalog.get("proposals", [])):
            scopes[tag] = scope
    blocked = set(personal.get("blocked", [])) | set(family.get("blocked", []))
    tags = [
        {
            "name": name,
            "scope": scopes.get(name, "family" if allowed == {"family"} else "personal"),
            "approved": bool(tagging_module.canonical_tag(name, [personal, family])),
            "blocks": counts.get(name, 0),
        }
        for name in sorted(set(scopes) | set(counts)) if name not in blocked
    ]
    tags.sort(key=lambda item: (-item["blocks"], item["name"]))
    return {"tags": tags, "count": len(tags)}


_WEEKDAYS = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")


def _personal_only(sources, what):
    if sources and "personal" not in sources:
        raise ToolUnavailable("{} steht nur im privaten Bereich zur Verfügung.".format(what))


def _date_bounds(start_date, end_date):
    from datetime import date

    try:
        start = date.fromisoformat(start_date) if start_date else None
        end = date.fromisoformat(end_date) if end_date else None
    except (TypeError, ValueError):
        raise ToolInputError("start_date und end_date müssen ISO-Daten sein (YYYY-MM-DD)") from None
    if start and end and start > end:
        raise ToolInputError("start_date darf nicht nach end_date liegen")
    return start, end


def run_list_days(user_id, *, sources=None, start_date=None, end_date=None, tags=None, limit=None):
    """Summarise which hashtags each journal day carries, without any block text.

    The bridge between a date and the vocabulary: a model can ask what a weekend
    was about, or in which days of a past year a topic appears, and only then
    fetch the blocks that matter. Family references carry a line number instead
    of a date and can never answer this, so the tool is private-only.
    """
    _sync_runtime()
    _personal_only(sources, "Die Tagesübersicht")
    start, end = _date_bounds(start_date, end_date)
    selected = _tag_list(tags) if tags else []

    days = {}
    for reference in tagging_module.references_for_tags(user_id, selected, sources=("personal",)):
        anchor = reference.get("anchor")
        if not isinstance(anchor, str):
            continue
        try:
            day = _DATE_TYPE.fromisoformat(anchor[:10])
        except ValueError:
            continue
        if (start and day < start) or (end and day > end):
            continue
        entry = days.setdefault(day, {"blocks": 0, "tags": {}})
        entry["blocks"] += 1
        for tag in reference.get("tags", []):
            entry["tags"][tag] = entry["tags"].get(tag, 0) + 1

    ordered = sorted(days)
    bounded = _bounded(limit, 62, MAX_DAY_LIMIT)
    truncated = len(ordered) > bounded
    # Keep the newest days when the range is wider than the caller asked for:
    # a truncated answer about last week beats one about last January.
    ordered = ordered[-bounded:]
    return {
        "days": [
            {
                "date": day.isoformat(),
                "weekday": _WEEKDAYS[day.weekday()],
                "blocks": days[day]["blocks"],
                "tags": dict(sorted(days[day]["tags"].items(), key=lambda item: (-item[1], item[0]))),
            }
            for day in ordered
        ],
        "count": len(ordered),
        "truncated": truncated,
    }


def run_get_day(user_id, *, start_date, sources=None, end_date=None, limit=None):
    """Return whole journal days, untagged blocks included.

    ``get_blocks`` can only reach what the hashtag index knows; a question about
    "the weekend" needs the blocks that were never tagged as well. The span is
    capped because a wide range would otherwise flood the model context.
    """
    _sync_runtime()
    _personal_only(sources, "Die Tagesansicht")
    start, end = _date_bounds(start_date, end_date or start_date)
    if (end - start).days + 1 > MAX_DAY_SPAN:
        raise ToolInputError("Höchstens {} Tage auf einmal".format(MAX_DAY_SPAN))

    blocks = []
    for document, block in brain_module.journal_day_blocks(
        user_id, start, end, limit=_bounded(limit, 100, MAX_BLOCK_LIMIT),
    ):
        text = (block.get("text") or "").strip()
        anchor = block.get("stable_anchor") or ""
        blocks.append({
            "source": document["source"],
            "path": document["path"],
            "date": document.get("date"),
            "anchor": anchor.split("timestamp:", 1)[-1] if anchor.startswith("timestamp:") else anchor,
            "tags": block.get("tags_auto", []),
            "text": text[:MAX_BLOCK_CHARS],
            "truncated": len(text) > MAX_BLOCK_CHARS,
        })
    return {"blocks": blocks, "count": len(blocks)}


def run_get_blocks(user_id, *, tags, sources=None, start_date=None, end_date=None, limit=None):
    """Return the individual journal blocks carrying every requested hashtag."""
    _sync_runtime()
    selected = _tag_list(tags)
    start, end = _date_bounds(start_date, end_date)

    blocks = []
    for document, block, reference in brain_module.resolve_tag_block_references(
        user_id, selected, start=start, end=end, limit=_bounded(limit, 50, MAX_BLOCK_LIMIT),
        sources=sources,
    ):
        text = (block.get("text") or "").strip()
        blocks.append({
            "source": document["source"],
            "path": document["path"],
            "date": document.get("date"),
            "anchor": reference.get("anchor"),
            "tags": reference.get("tags", []),
            "ai_tags": reference.get("ai_tags", []),
            "text": text[:MAX_BLOCK_CHARS],
            "truncated": len(text) > MAX_BLOCK_CHARS,
        })
    return {"blocks": blocks, "count": len(blocks)}


__all__ = [
    "ToolInputError", "ToolUnavailable", "search_available", "run_search", "run_get_blocks",
    "run_list_tags",
    "run_list_days", "run_get_day",
    "MAX_SEARCH_LIMIT", "MAX_BLOCK_LIMIT", "MAX_DAY_LIMIT", "MAX_DAY_SPAN",
]
