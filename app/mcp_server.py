"""Model Context Protocol endpoint for the journal search tools.

AI maintenance contract: transport, protocol versioning and authentication only.
Tool behaviour belongs in mcp_tools.py.

The endpoint is dual-era on purpose.  Revision 2026-07-28 dropped the
``initialize`` handshake and carries the protocol version as per-request
metadata, while widely deployed clients still open with ``initialize``.  A
request is served as modern when it declares a modern version, as legacy when it
opens with ``initialize`` or names a legacy version, so both keep working
against one URL.

Authentication reuses a per-user Share-API bearer key; there is deliberately no
cookie or CSRF path, so a browser cannot be tricked into calling this endpoint on
a logged-in user's behalf.

The blueprint serves two areas that never overlap: ``/mcp/privat`` (alias
``/mcp``, scope ``mcp``) reaches only the caller's own journals, ``/mcp/family``
(scope ``mcp_family``) only the shared family documents. The area is chosen by
the route and passed to the tools; a client cannot widen it through arguments.
"""

import base64
import binascii
import json
import os
import sys
import urllib.parse

from flask import Blueprint, jsonify, request

import mcp_tools

_core = sys.modules.get("main") or sys.modules.get("__main__")
if _core is None:  # pragma: no cover - import order is a hard application invariant
    raise RuntimeError("main facade must exist before mcp_server is imported")

mcp_bp = Blueprint("mcp", __name__, url_prefix="/mcp")

VERSION_META_KEY = "io.modelcontextprotocol/protocolVersion"
SERVER_INFO_META_KEY = "io.modelcontextprotocol/serverInfo"
MODERN_VERSIONS = ("2026-07-28",)
LEGACY_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
SUPPORTED_VERSIONS = MODERN_VERSIONS + LEGACY_VERSIONS
PREFERRED_LEGACY_VERSION = "2025-11-25"

UNSUPPORTED_PROTOCOL_VERSION = -32022
HEADER_MISMATCH = -32020
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
PARSE_ERROR = -32700

INSTRUCTIONS = (
    "Durchsucht das persönliche Journal des authentifizierten Journl-Nutzers. "
    "'list_tags' nennt die verfügbaren Hashtags mit Trefferzahl und ist der "
    "Einstieg, wenn der passende Tag noch unbekannt ist; 'list_days' zeigt, "
    "welche Hashtags an welchem Tag vorkommen, und ist der Einstieg bei Fragen "
    "nach einem Zeitraum. 'get_blocks' liefert dann die Blöcke zu bestimmten "
    "Hashtags, 'get_day' einen ganzen Tag samt ungetaggter Blöcke und 'search' "
    "rankt Journaltage per Volltext. 'semantic_search' findet Stellen nach "
    "Bedeutung statt Wortlaut (Fragen in Alltagssprache, Umschreibungen), "
    "'find_similar' verwandte Einträge zu einem Treffer. Familiendokumente sind "
    "hier nicht enthalten."
)

FAMILY_INSTRUCTIONS = (
    "Durchsucht ausschließlich die gemeinsamen Familiendokumente der Journl-"
    "Instanz — persönliche Journale sind hier weder sichtbar noch durchsuchbar. "
    "'list_tags' nennt die Hashtags der Familie mit Trefferzahl, 'get_blocks' "
    "liefert die zugehörigen Abschnitte, 'semantic_search' und 'find_similar' "
    "suchen nach Bedeutung. Familienzeilen tragen kein Datum, "
    "Datumsfilter bleiben deshalb wirkungslos."
)

# One private and one family server behind one blueprint. The split is a hard
# boundary in both directions: each side carries its own token scope, so a key
# for the family assistant cannot read a personal journal, and each side only
# ever resolves references from its own source.
AREAS = {
    "personal": {
        "scope": "mcp",
        "sources": ("personal",),
        "tools": ("list_tags", "list_days", "get_blocks", "get_day", "search", "semantic_search", "find_similar"),
        "instructions": INSTRUCTIONS,
        "server_name": "journl",
    },
    "family": {
        "scope": "mcp_family",
        "sources": ("family",),
        "tools": ("list_tags", "get_blocks", "semantic_search", "find_similar"),
        "instructions": FAMILY_INSTRUCTIONS,
        "server_name": "journl-family",
    },
}

_DATE_SCHEMA = {"type": "string", "description": "ISO-Datum YYYY-MM-DD", "format": "date"}
_TAGS_SCHEMA = {
    "type": "array", "items": {"type": "string"}, "minItems": 1,
    "description": "Hashtags ohne '#'. Ein Treffer muss alle angegebenen Tags tragen.",
}

SEARCH_TOOL = {
    "name": "search",
    "title": "Journal-Volltextsuche",
    "description": (
        "Sucht per deutscher Volltextsuche in den Journalen des Nutzers und liefert "
        "gerankte Treffer mit hervorgehobenem Textausschnitt. Optional auf Hashtags "
        "und einen Zeitraum eingrenzbar."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Suchbegriffe; \"...\" und -Ausschluss werden unterstützt."},
            "tags": _TAGS_SCHEMA,
            "start_date": _DATE_SCHEMA,
            "end_date": _DATE_SCHEMA,
            "limit": {"type": "integer", "minimum": 1, "maximum": mcp_tools.MAX_SEARCH_LIMIT, "default": 30},
            "offset": {"type": "integer", "minimum": 0, "default": 0},
        },
        "required": ["query"],
        "additionalProperties": False,
    },
}

LIST_TAGS_TOOL = {
    "name": "list_tags",
    "title": "Verfügbare Hashtags",
    "description": (
        "Nennt alle Hashtags des Nutzers — freigegebene wie noch vorgeschlagene — "
        "mit Geltungsbereich und der Anzahl Journalblöcke, die den Tag tragen. "
        "Vor 'get_blocks' aufrufen, wenn der passende Hashtag noch unbekannt ist."
    ),
    "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
}

LIST_DAYS_TOOL = {
    "name": "list_days",
    "title": "Tage mit ihren Hashtags",
    "description": (
        "Nennt je Journaltag die Hashtags mit Anzahl — ohne Blocktext. Beantwortet "
        "'was war am Wochenende los' und, mit 'tags' eingegrenzt, 'an welchen Tagen "
        "ging es um X'. Danach gezielt 'get_blocks' oder 'get_day' aufrufen."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "start_date": _DATE_SCHEMA,
            "end_date": _DATE_SCHEMA,
            "tags": {**_TAGS_SCHEMA, "description": "Optional: nur Tage, die alle diese Hashtags tragen."},
            "limit": {
                "type": "integer", "minimum": 1, "maximum": mcp_tools.MAX_DAY_LIMIT, "default": 62,
                "description": "Höchstzahl Tage; bei Überschreitung werden die neuesten behalten.",
            },
        },
        "additionalProperties": False,
    },
}

GET_DAY_TOOL = {
    "name": "get_day",
    "title": "Ganze Journaltage",
    "description": (
        "Liefert alle Blöcke eines Tages oder einer kurzen Spanne — auch die ohne "
        "Hashtag, die 'get_blocks' nicht finden kann. Höchstens {} Tage je Aufruf."
    ).format(mcp_tools.MAX_DAY_SPAN),
    "inputSchema": {
        "type": "object",
        "properties": {
            "start_date": _DATE_SCHEMA,
            "end_date": {**_DATE_SCHEMA, "description": "Optional; ohne Angabe genau start_date."},
            "limit": {"type": "integer", "minimum": 1, "maximum": mcp_tools.MAX_BLOCK_LIMIT, "default": 100},
        },
        "required": ["start_date"],
        "additionalProperties": False,
    },
}

GET_BLOCKS_TOOL = {
    "name": "get_blocks",
    "title": "Journalblöcke nach Hashtag",
    "description": (
        "Liefert die einzelnen Journalblöcke, die alle angegebenen Hashtags tragen — "
        "wahlweise für ein einzelnes Datum (start_date = end_date) oder einen Zeitraum. "
        "Gedacht, um gezielt Journalinhalte in den Kontext zu holen."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "tags": _TAGS_SCHEMA,
            "start_date": _DATE_SCHEMA,
            "end_date": _DATE_SCHEMA,
            "limit": {"type": "integer", "minimum": 1, "maximum": mcp_tools.MAX_BLOCK_LIMIT, "default": 50},
        },
        "required": ["tags"],
        "additionalProperties": False,
    },
}

_SEMANTIC_LIMIT_SCHEMA = {"type": "integer", "minimum": 1, "maximum": mcp_tools.MAX_SEMANTIC_LIMIT, "default": 10}
_INCLUDE_SCHEMA = {
    "type": "array", "items": {"type": "string", "enum": ["journal", "notes", "agent"]}, "minItems": 1,
    "description": (
        "Nur im privaten Bereich: Quellen (journal, notes = Projekte/Notizen, agent = Agent- und "
        "Chatverläufe). Ohne Angabe die Auswahl aus den Einstellungen des Nutzers."
    ),
}

SEMANTIC_SEARCH_TOOL = {
    "name": "semantic_search",
    "title": "Suche nach Bedeutung",
    "description": (
        "Findet Journalstellen nach Bedeutung statt Wortlaut — auch wenn die Frage andere "
        "Wörter benutzt als der Eintrag (\"wann hatte ich Rückenprobleme\" findet "
        "\"Bandscheibe zwickt\"). Standard ist hybrid: Wort- und Bedeutungstreffer gemischt. "
        "Jeder Treffer nennt path und anchor für 'find_similar' sowie match "
        "(word, semantic, both)."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Frage oder Stichworte in Alltagssprache."},
            "include": _INCLUDE_SCHEMA,
            "start_date": _DATE_SCHEMA,
            "end_date": _DATE_SCHEMA,
            "mode": {"type": "string", "enum": ["hybrid", "semantic"], "default": "hybrid"},
            "limit": _SEMANTIC_LIMIT_SCHEMA,
        },
        "required": ["query"],
        "additionalProperties": False,
    },
}

FIND_SIMILAR_TOOL = {
    "name": "find_similar",
    "title": "Ähnliche Einträge",
    "description": (
        "Liefert Einträge, die einem bekannten Block inhaltlich ähneln — mit dessen "
        "gespeichertem Vektor, ohne neue Suche. path und anchor stammen aus einem Treffer "
        "von 'semantic_search' oder 'get_blocks'."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "anchor": {"type": "string"},
            "include": _INCLUDE_SCHEMA,
            "limit": _SEMANTIC_LIMIT_SCHEMA,
        },
        "required": ["path", "anchor"],
        "additionalProperties": False,
    },
}


def _result(rpc_id, payload, modern):
    body = {"jsonrpc": "2.0", "id": rpc_id, "result": dict(payload)}
    if modern:
        body["result"].setdefault("resultType", "complete")
    return jsonify(body)


def _error(rpc_id, code, message, data=None, status=200):
    error = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return jsonify({"jsonrpc": "2.0", "id": rpc_id, "error": error}), status


def _decoded_header(value):
    """Undo the ``=?base64?...?=`` sentinel clients use for non-ASCII header values."""
    if isinstance(value, str) and value.startswith("=?base64?") and value.endswith("?="):
        try:
            return base64.b64decode(value[9:-2], validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError, ValueError):
            return None
    return value


def _allowed_origin():
    """Reject cross-origin browser callers to blunt DNS-rebinding attempts."""
    origin = request.headers.get("Origin")
    if not origin:
        return True
    allowed = {
        item.strip().casefold()
        for item in os.environ.get("JOURNL_MCP_ALLOWED_ORIGINS", "").split(",")
        if item.strip()
    }
    if origin.casefold() in allowed:
        return True
    host = urllib.parse.urlsplit(origin).netloc.casefold()
    return bool(host) and host == (request.host or "").casefold()


def _declared_version(params):
    meta = params.get("_meta") if isinstance(params.get("_meta"), dict) else {}
    body_version = meta.get(VERSION_META_KEY)
    header_version = request.headers.get("MCP-Protocol-Version")
    return body_version, header_version


def _modern_header_error(rpc_id, method, params, body_version, header_version):
    """Enforce the header/body agreement modern clients must provide."""
    if not header_version:
        return _error(rpc_id, HEADER_MISMATCH, "MCP-Protocol-Version header fehlt", status=400)
    if body_version and header_version != body_version:
        return _error(
            rpc_id, HEADER_MISMATCH,
            "MCP-Protocol-Version header stimmt nicht mit _meta überein", status=400,
        )
    declared_method = request.headers.get("Mcp-Method")
    if not declared_method:
        return _error(rpc_id, HEADER_MISMATCH, "Mcp-Method header fehlt", status=400)
    if declared_method != method:
        return _error(rpc_id, HEADER_MISMATCH, "Mcp-Method header stimmt nicht mit method überein", status=400)
    if method == "tools/call":
        declared_name = _decoded_header(request.headers.get("Mcp-Name"))
        if declared_name is None:
            return _error(rpc_id, HEADER_MISMATCH, "Mcp-Name header fehlt oder ist ungültig kodiert", status=400)
        if declared_name != params.get("name"):
            return _error(rpc_id, HEADER_MISMATCH, "Mcp-Name header stimmt nicht mit params.name überein", status=400)
    return None


def tools_for(area):
    """The tool definitions one area offers. Also read by the in-app chat."""
    offered = area["tools"]
    tools = [
        tool for tool in (LIST_TAGS_TOOL, LIST_DAYS_TOOL, GET_BLOCKS_TOOL, GET_DAY_TOOL)
        if tool["name"] in offered
    ]
    if "search" in offered and mcp_tools.search_available():
        tools.append(SEARCH_TOOL)
    if "semantic_search" in offered and mcp_tools.semantic_available():
        tools.extend((SEMANTIC_SEARCH_TOOL, FIND_SIMILAR_TOOL))
    return tools


def _capabilities():
    return {"tools": {}}


def _server_info(area):
    return {"name": area["server_name"], "version": _core.load_app_version()}


def _call_tool(rpc_id, user_id, params, modern, area):
    name = params.get("name")
    arguments = params.get("arguments") or {}
    if not isinstance(arguments, dict):
        return _error(rpc_id, INVALID_PARAMS, "arguments muss ein Objekt sein")
    runner = {
        "search": mcp_tools.run_search,
        "get_blocks": mcp_tools.run_get_blocks,
        "list_tags": mcp_tools.run_list_tags,
        "list_days": mcp_tools.run_list_days,
        "get_day": mcp_tools.run_get_day,
        "semantic_search": mcp_tools.run_semantic_search,
        "find_similar": mcp_tools.run_find_similar,
    }.get(name) if name in area["tools"] else None
    if runner is None:
        return _error(rpc_id, INVALID_PARAMS, "Unbekanntes Tool: {}".format(name))
    try:
        # ``sources`` is set by the endpoint, never by the caller: a client that
        # sends it too collides with this keyword and is rejected below.
        payload = runner(user_id, sources=area["sources"], **arguments)
    except (mcp_tools.ToolInputError, mcp_tools.ToolUnavailable) as exc:
        # Actionable for the model, so it is a tool error rather than a protocol error.
        return _result(rpc_id, {
            "content": [{"type": "text", "text": str(exc)}], "isError": True,
        }, modern)
    except TypeError as exc:
        return _error(rpc_id, INVALID_PARAMS, "Ungültige Argumente: {}".format(exc))
    return _result(rpc_id, {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
        "structuredContent": payload,
        "isError": False,
    }, modern)


@mcp_bp.route("", methods=["POST", "GET", "DELETE"])
@mcp_bp.route("/", methods=["POST", "GET", "DELETE"])
@mcp_bp.route("/privat", methods=["POST", "GET", "DELETE"])
def mcp_endpoint():
    """Private server. ``/mcp`` stays as its alias so configured clients keep working."""
    return _serve(AREAS["personal"])


@mcp_bp.route("/family", methods=["POST", "GET", "DELETE"])
def mcp_family_endpoint():
    return _serve(AREAS["family"])


def _serve(area):
    if not _allowed_origin():
        return _error(None, HEADER_MISMATCH, "Origin ist nicht erlaubt", status=403)
    if request.method != "POST":
        # Revision 2026-07-28 removed the GET stream and the DELETE session teardown.
        return _error(None, METHOD_NOT_FOUND, "Nur POST wird unterstützt", status=405)

    user = _core.share_api_module._token_user({area["scope"]})
    if not user:
        response = jsonify({"error": "Ungültiger MCP-Schlüssel oder fehlende Rechte"})
        response.headers["WWW-Authenticate"] = 'Bearer realm="Journl MCP"'
        return response, 401

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _error(None, PARSE_ERROR, "Ungültiger JSON-RPC-Körper", status=400)
    method = payload.get("method")
    rpc_id = payload.get("id")
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    body_version, header_version = _declared_version(params)
    declared = body_version or header_version

    if method == "initialize":
        requested = params.get("protocolVersion")
        agreed = requested if requested in LEGACY_VERSIONS else PREFERRED_LEGACY_VERSION
        return _result(rpc_id, {
            "protocolVersion": agreed,
            "capabilities": _capabilities(),
            "serverInfo": _server_info(area),
            "instructions": area["instructions"],
        }, modern=False)

    modern = declared in MODERN_VERSIONS
    if declared and declared not in SUPPORTED_VERSIONS:
        return _error(
            rpc_id, UNSUPPORTED_PROTOCOL_VERSION, "Nicht unterstützte Protokollversion",
            data={"supported": list(SUPPORTED_VERSIONS), "requested": declared}, status=400,
        )
    if modern:
        header_error = _modern_header_error(rpc_id, method, params, body_version, header_version)
        if header_error:
            return header_error

    if "id" not in payload:
        # Notifications (such as the legacy notifications/initialized) carry no reply.
        return "", 202
    if method == "server/discover":
        return _result(rpc_id, {
            "supportedVersions": list(SUPPORTED_VERSIONS),
            "capabilities": _capabilities(),
            "instructions": area["instructions"],
            "_meta": {SERVER_INFO_META_KEY: _server_info(area)},
        }, modern)
    if method == "ping":
        return _result(rpc_id, {}, modern)
    if method == "tools/list":
        return _result(rpc_id, {"tools": tools_for(area)}, modern)
    if method == "tools/call":
        return _call_tool(rpc_id, user["id"], params, modern, area)
    return _error(
        rpc_id, METHOD_NOT_FOUND, "Unbekannte Methode: {}".format(method),
        status=404 if modern else 200,
    )


__all__ = ["mcp_bp", "AREAS", "SUPPORTED_VERSIONS", "MODERN_VERSIONS", "LEGACY_VERSIONS", "tools_for"]
