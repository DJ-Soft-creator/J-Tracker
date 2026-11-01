"""„Chat with your Notes": Verbindungseinstellungen und die Werkzeugschleife.

AI maintenance contract: dieses Modul haelt Konfiguration und Modelldialog.
HTTP-Validierung gehoert in brain_chat_routes.py, die Journal-Werkzeuge selbst
in mcp_tools.py.

Der Chat ist ein App-interner MCP-Client ohne Token: er ruft dieselben Runner
wie der Endpunkt /mcp/privat direkt in der Sitzung des angemeldeten Benutzers
auf und setzt ``sources`` fest auf ("personal",). Ein Share-API-Schluessel ist
dafuer bewusst nicht noetig — und der Chat kann damit auch nie mehr sehen als
der Endpunkt: derselbe Bereich, dieselben Sichtbarkeitsregeln, dieselben
Deckelungen.

Optional — je ein Schalter in den Einstellungen, Default aus — bietet der Chat
zusaetzlich Werkzeuge fuer Projekte und Notes an (brain_chat_documents.py).
Diese sehen eigene Dateien und genau die Familiendateien, die der Nutzer auch
in der Brain-Ansicht sieht. Sind beide Schalter aus, sind Werkzeugliste und
System-Prompt exakt die des reinen Journal-Chats.

Der Endpunkt ist ein OpenAI-kompatibler ``/v1/chat/completions`` (llama-swap,
llama.cpp, LM Studio, Ollama). Die Basis-URL wird wie bei den Agent-Projekten
auf das lokale Netz begrenzt, damit Journalinhalte den Host nicht verlassen.
"""

import json
import logging
import sys

import agent_loop
import agent_projects
import background_runs
import brain_chat_documents
import brain_chat_sessions
import mcp_server
import mcp_tools
from agent_projects import AgentProjectError
from scheduling import read_text_file, write_text_file

logger = logging.getLogger(__name__)

_core = sys.modules.get("main") or sys.modules.get("__main__")
if _core is None:  # pragma: no cover - import order is a hard application invariant
    raise RuntimeError("main facade must exist before brain_chat is imported")

STORE_NAME = "brain-chat.json"
SOURCES = ("personal",)

# Die Deckelungen der Schleife gelten fuer jeden Agenten gleich und stehen
# deshalb in agent_loop. Hier nur weitergereicht, weil die Oberflaeche
# MAX_ROUNDS anzeigt.
MAX_ROUNDS = agent_loop.MAX_ROUNDS
MAX_HISTORY_MESSAGES = agent_loop.MAX_HISTORY_MESSAGES
MAX_MESSAGE_CHARS = agent_loop.MAX_MESSAGE_CHARS
MAX_TOOL_RESULT_CHARS = agent_loop.MAX_TOOL_RESULT_CHARS
REQUEST_TIMEOUT = agent_loop.REQUEST_TIMEOUT
MAX_TOKENS = agent_loop.MAX_TOKENS
TEMPERATURE = agent_loop.TEMPERATURE

SYSTEM_PROMPT = (
    "Du bist der Journal-Assistent von Journl und beantwortest Fragen zum "
    "persoenlichen Journal des angemeldeten Nutzers.\n\n"
    "{instructions}{extras}\n\n"
    "Regeln:\n"
    "- Antworte ausschliesslich auf Basis der Werkzeugergebnisse. Rate nichts "
    "und erfinde keine Journalinhalte.\n"
    "- Findest du nichts, sag genau das.\n"
    "- Rufe Werkzeuge ueber die Tool-Schnittstelle auf, niemals als Text in "
    "deiner Antwort.\n"
    "- Nenne Datumsangaben im Format YYYY-MM-DD.\n"
    "- Antworte auf Deutsch und fasse dich kurz.\n"
    "Heutiges Datum: {today}."
)


# Nur angehaengt, wenn der jeweilige Schalter an ist.
EXTRA_PROMPTS = {
    "project": (
        "Zusaetzlich sind die Projekte des Nutzers verfuegbar — eigene und fuer ihn "
        "sichtbare Familienprojekte: 'list_projects' listet sie, 'get_project' liest "
        "Aufgaben mit Status und Kommentare."
    ),
    "note": (
        "Zusaetzlich sind die Notes des Nutzers verfuegbar — eigene und fuer ihn "
        "sichtbare Familiennotizen: 'list_notes' listet sie, 'get_note' liest eine Notiz."
    ),
}
EXTRA_SOURCE_RULE = (
    "Nenne bei Antworten aus Projekten oder Notes deren Titel und Quelle "
    "(Privat/Familie)."
)

# Schalter in brain-chat.json -> Art der Dateien, die er freigibt.
FLAG_KINDS = {"include_projects": "project", "include_notes": "note"}
# Suche mit oder ohne Embedding-Modell: an heisst, der Chat bekommt zusaetzlich
# semantic_search/find_similar. Greift nur, wenn die semantische Suche fuer das
# Konto an ist — sonst melden die Werkzeuge das selbst.
SEMANTIC_FLAG = "semantic_search"
SEMANTIC_TOOLS = ("semantic_search", "find_similar")
FLAGS = (*FLAG_KINDS, SEMANTIC_FLAG)


class BrainChatError(agent_loop.AgentError):
    """Fehler, den die Oberflaeche dem Nutzer wortwoertlich zeigen darf."""


# ─── Einstellungen ────────────────────────────────────────────────────
def _settings_path(user_id):
    return _core.DATA_DIR / user_id / ".config" / STORE_NAME


def _empty_settings():
    return {"base_url": "", "model": "", "api_key": "", **{flag: False for flag in FLAGS}}


def load_settings(user_id):
    """Read the stored connection, including the secret. Server-side only.

    Die Schalter fuer Projekte und Notes gelten nur bei einem echten ``true``;
    alles andere — auch eine aeltere Datei ohne die Felder — heisst aus.
    """
    path = _settings_path(user_id)
    if not path.is_file() or path.is_symlink():
        return _empty_settings()
    try:
        stored = json.loads(read_text_file(path))
    except (OSError, json.JSONDecodeError):
        return _empty_settings()
    if not isinstance(stored, dict):
        return _empty_settings()
    return {
        "base_url": str(stored.get("base_url") or ""),
        "model": str(stored.get("model") or ""),
        "api_key": str(stored.get("api_key") or ""),
        **{flag: stored.get(flag) is True for flag in FLAGS},
    }


def enabled_kinds(settings):
    """Die Dateiarten, deren Schalter an ist — in fester Reihenfolge."""
    return tuple(kind for flag, kind in FLAG_KINDS.items() if (settings or {}).get(flag) is True)


def public_settings(settings):
    """The view the browser may see: the key itself never leaves the server."""
    return {
        "base_url": settings["base_url"],
        "model": settings["model"],
        "has_api_key": bool(settings["api_key"]),
        "configured": bool(settings["base_url"] and settings["model"]),
        **{flag: bool(settings.get(flag)) for flag in FLAGS},
    }


def _write_settings(user_id, settings):
    stored = {"schema": 1, **settings}
    write_text_file(_settings_path(user_id), json.dumps(stored, ensure_ascii=False, indent=2) + "\n")
    return settings


def save_settings(user_id, raw):
    """Validate and persist one connection.

    ``api_key`` is optional and sticky: a payload without the field keeps the
    stored key, an empty string clears it. So the browser can save a changed
    model without ever having seen the secret.

    Die Schalter ``include_projects``/``include_notes``/``semantic_search`` sind ebenso sticky. Ein
    Payload, der nur Schalter traegt, aendert nur die Schalter — der Chat legt
    sie je Frage um, ohne Endpunkt und Modell erneut zu schicken.
    """
    if not isinstance(raw, dict):
        raise BrainChatError("Ungueltige Einstellungen.")
    current = load_settings(user_id)
    flags = {}
    for flag in FLAGS:
        if flag not in raw:
            flags[flag] = current[flag]
        elif isinstance(raw[flag], bool):
            flags[flag] = raw[flag]
        else:
            raise BrainChatError("Ungueltiger Schalter: {}".format(flag))
    if not ({"base_url", "model", "api_key"} & set(raw)):
        if not (set(FLAGS) & set(raw)):
            raise BrainChatError("Ungueltige Einstellungen.")
        return _write_settings(user_id, {**current, **flags})
    try:
        base_url = agent_projects.validated_base_url(raw.get("base_url"))
    except AgentProjectError as exc:
        raise BrainChatError(str(exc)) from exc
    model = str(raw.get("model") or "").strip()
    if not agent_projects.valid_model_name(model):
        raise BrainChatError("Bitte ein Modell waehlen.")
    if "api_key" in raw:
        api_key = str(raw.get("api_key") or "").strip()
        if len(api_key) > 400:
            raise BrainChatError("Der API-Schluessel ist zu lang.")
    else:
        api_key = current["api_key"]
    return _write_settings(user_id, {"base_url": base_url, "model": model, "api_key": api_key, **flags})


def discover_models(base_url):
    """Live model list of one endpoint — the same discovery the projects use."""
    try:
        return agent_projects.discover_models("openai", base_url)
    except AgentProjectError as exc:
        raise BrainChatError(str(exc)) from exc


# ─── Werkzeuge ────────────────────────────────────────────────────────
_RUNNERS = {
    "search": mcp_tools.run_search,
    "get_blocks": mcp_tools.run_get_blocks,
    "list_tags": mcp_tools.run_list_tags,
    "list_days": mcp_tools.run_list_days,
    "get_day": mcp_tools.run_get_day,
    "semantic_search": mcp_tools.run_semantic_search,
    "find_similar": mcp_tools.run_find_similar,
}


def _function(tool):
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool["description"],
            "parameters": tool["inputSchema"],
        },
    }


def tool_specs(settings=None):
    """The private MCP tools in OpenAI function shape.

    Derived from the endpoint's own definitions instead of a second copy, so a
    changed schema can never drift between the MCP client and this chat. The
    project and note tools follow only while their switch in ``settings`` is on,
    the semantic tools only while ``semantic_search`` is.
    """
    semantic = (settings or {}).get(SEMANTIC_FLAG) is True
    specs = [
        _function(tool) for tool in mcp_server.tools_for(mcp_server.AREAS["personal"])
        if semantic or tool["name"] not in SEMANTIC_TOOLS
    ]
    for kind in enabled_kinds(settings):
        specs.extend(_function(tool) for tool in brain_chat_documents.TOOLS[kind])
    return specs


def run_tool(user_id, name, arguments, settings=None):
    """Run one journal tool in the caller's own area.

    ``sources`` is fixed here exactly as the endpoint fixes it: the model names
    a tool, never a scope. A project or note tool runs only while its switch is
    on — a model that names it anyway gets the answer for an unknown tool.
    """
    document_kind = brain_chat_documents.KIND_OF_TOOL.get(name)
    if document_kind and document_kind in enabled_kinds(settings):
        runner, scope = brain_chat_documents.RUNNERS[name], {}
    elif name in SEMANTIC_TOOLS and (settings or {}).get(SEMANTIC_FLAG) is not True:
        runner, scope = None, {}
    else:
        runner, scope = _RUNNERS.get(name), {"sources": SOURCES}
    if runner is None:
        raise mcp_tools.ToolInputError("Unbekanntes Werkzeug: {}".format(name))
    if not isinstance(arguments, dict):
        raise mcp_tools.ToolInputError("arguments muss ein Objekt sein")
    try:
        return runner(user_id, **scope, **arguments)
    except TypeError as exc:
        raise mcp_tools.ToolInputError("Ungueltige Argumente: {}".format(exc)) from exc


def _step_summary(name, payload):
    """A short, printable trace of what a tool actually returned."""
    if not isinstance(payload, dict):
        return {"tool": name}
    step = {"tool": name}
    for field in ("count", "total", "truncated"):
        if field in payload:
            step[field] = payload[field]
    dates = []
    for item in (payload.get("days") or payload.get("blocks") or []):
        value = isinstance(item, dict) and (item.get("date") or item.get("path"))
        if value and value not in dates:
            dates.append(value)
    if dates:
        step["hits"] = dates[:20]
    if name == "list_tags":
        step["hits"] = [tag["name"] for tag in (payload.get("tags") or [])[:20]]
    if name in ("list_projects", "list_notes"):
        step["hits"] = [item["title"] for item in (payload.get("projects") or payload.get("notes") or [])[:20]]
    elif name in ("get_project", "get_note") and payload.get("title"):
        step["hits"] = [payload["title"]]
    return step


# ─── Modellaufruf ────────────────────────────────────────────────────
def _post(settings, body):
    """Der Endpunktaufruf dieses Chats.

    Bleibt eine benannte Funktion dieses Moduls, obwohl die Mechanik in
    agent_loop steht: sie ist der Punkt, an dem Tests den Endpunkt ersetzen.
    """
    return agent_loop.post(settings, body, BrainChatError)


def normalise_history(raw):
    """Accept only the two roles the browser is allowed to replay."""
    return agent_loop.normalise_history(raw, BrainChatError)


class _ChatProfile(agent_loop.Profile):
    """Die Journalwerkzeuge in der Sitzung genau eines Benutzers."""

    error_class = BrainChatError
    recoverable_errors = (mcp_tools.ToolInputError, mcp_tools.ToolUnavailable)
    unconfigured_message = (
        "Es ist noch keine Verbindung konfiguriert. "
        "Einstellungen → Brain → Chat with your Notes."
    )

    def __init__(self, user_id, settings=None):
        self.user_id = user_id
        self.settings = settings or {}

    def system_prompt(self):
        today = _core.get_tz_aware_now()[0].strftime("%Y-%m-%d")
        kinds = enabled_kinds(self.settings)
        extras = "".join("\n\n" + EXTRA_PROMPTS[kind] for kind in kinds)
        if kinds:
            extras += "\n" + EXTRA_SOURCE_RULE
        return SYSTEM_PROMPT.format(
            instructions=mcp_server.AREAS["personal"]["instructions"], extras=extras, today=today,
        )

    def tool_specs(self):
        return tool_specs(self.settings)

    def run_tool(self, name, arguments):
        return run_tool(self.user_id, name, arguments, self.settings)

    def step_summary(self, name, payload):
        return _step_summary(name, payload)

    def post(self, settings, body):
        return _post(settings, body)


def answer(user_id, history, settings, on_step=None):
    """Run the tool-calling loop and return the final answer plus its trace.

    ``on_step`` sieht jeden Werkzeugschritt, sobald er fertig ist. Ein
    serverseitiger Lauf kann damit zeigen, woran das Modell gerade sucht,
    statt minutenlang nur „arbeitet" zu melden.
    """
    return agent_loop.run(_ChatProfile(user_id, settings), history, settings, on_step=on_step)


# ─── Serverseitiger Lauf ──────────────────────────────────────────────
# Eine Antwort kann mehrere Werkzeugrunden und Minuten brauchen. Als einzelner
# Request war sie an den offenen Browser gebunden: sperrte sich das Handy, war
# die fertige Antwort verloren. Jetzt laeuft die Frage als Auftrag im Server,
# landet in der Chatdatei und wartet dort auf den naechsten Blick.
RUN_KIND = "brain_chat_ask"


def ask_handler(handle, params):
    """Eine Frage beantworten und den Turn an die Chatdatei anhaengen."""
    settings = load_settings(handle.user_id)
    try:
        history = normalise_history(params.get("messages"))
    except BrainChatError as exc:
        raise background_runs.RunError(str(exc)) from exc

    counter = {"steps": 0}

    def on_step(step):
        counter["steps"] += 1
        handle.log(step)
        handle.progress(state="asking", tool_calls=counter["steps"])

    handle.progress(state="asking", tool_calls=0)
    try:
        result = answer(handle.user_id, history, settings, on_step=on_step)
    except BrainChatError as exc:
        raise background_runs.RunError(str(exc)) from exc

    session, session_error = None, ""
    try:
        session = brain_chat_sessions.append_turn(
            handle.user_id, params.get("session_path", ""), history[-1]["content"],
            result["answer"], result["steps"], model=result.get("model", ""),
            truncated=result.get("truncated", False),
        )
    except OSError as exc:
        # Die Antwort ist das Wertvolle. Sie deshalb zu verwerfen, weil die
        # Ablage klemmt, waere die falsche Reihenfolge.
        logger.exception("Chat session could not be written for %s", handle.user_id)
        session_error = "Der Verlauf konnte nicht gespeichert werden: {}".format(exc)
    if session and getattr(_core, "brain_module", None):
        # Der neue Verlauf soll in der Brain-Suche auftauchen, nicht erst beim
        # naechsten Turnus des Indexers.
        _core.brain_module.enqueue_rebuild(handle.user_id)
    return {**result, "session": session or {}, "session_error": session_error}


background_runs.register(RUN_KIND, ask_handler)


__all__ = [
    "BrainChatError", "FLAG_KINDS", "FLAGS", "SEMANTIC_FLAG", "MAX_ROUNDS", "RUN_KIND", "answer", "ask_handler",
    "discover_models", "enabled_kinds", "load_settings", "normalise_history", "public_settings", "run_tool", "save_settings",
    "tool_specs",
]
