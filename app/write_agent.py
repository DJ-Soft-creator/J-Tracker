"""Der Schreib-Agent: Werkzeuge auf genau einem Text, nie auf einer Datei.

AI maintenance contract: dieses Modul haelt die Werkzeuge und den
serverseitigen Lauf. Die Werkzeugschleife steht in agent_loop.py, die
HTTP-Validierung in write_agent_routes.py, die Wissensnotizen in
agent_knowledge.py.

Der entscheidende Unterschied zum Einkaufslisten-Agenten: **hier wird nichts
gespeichert.** Der Text kommt mit dem Auftrag aus dem Browser, das Ergebnis
geht mit der Antwort zurueck, und erst der Nutzer entscheidet im Fenster, ob
er ihn uebernimmt. Das ist kein Versehen, sondern der Grund, warum der Agent
auch auf einen Journaleintrag aus dem Heute- oder Brain-Tab losgelassen werden
darf: ein Modell, das dort direkt schreiben duerfte, koennte einen bereits
abgelegten Eintrag stillschweigend umschreiben.

Daraus folgt der zweite Unterschied: der Agent braucht keinen Rueckwaertsgang.
Was er vorschlaegt, existiert nur im Fenster, bis es jemand uebernimmt — und
das Rueckgaengig dafuer ist die Undo-Historie des Schreibfeldes.

Die Schreibziele (``ai_functions`` der config.json) sind dieselben, die der
KI-Modus im Schreiben-Tab benutzt. Ihr ``system_prompt`` wird dem Systemtext
vorangestellt, ihr ``mode`` sagt dem Modell, ob es ersetzen oder ergaenzen
soll. Nachgebaut wird davon nichts.
"""

import logging
import sys

import agent_knowledge
import agent_loop
import background_runs
import mcp_tools
import shopping_agent

logger = logging.getLogger(__name__)

_core = sys.modules.get("main") or sys.modules.get("__main__")
if _core is None:  # pragma: no cover - import order is a hard application invariant
    raise RuntimeError("main facade must exist before write_agent is imported")

MAX_ROUNDS = agent_loop.MAX_ROUNDS
MAX_TEXT_CHARS = 40000
MAX_SELECTED_TAGS = 12
RUN_KIND = "write_agent"

SYSTEM_PROMPT = (
    "Du bist der Schreib-Assistent von Journl und arbeitest an genau einem "
    "Text — dem, den der Nutzer gerade vor sich hat.\n\n"
    "Regeln:\n"
    "- Lies den Text immer zuerst mit get_text.\n"
    "- Aenderungen gibst du ausschliesslich ueber set_text zurueck, mit dem "
    "**vollstaendigen** neuen Text. Schreib den Text nicht in deine Antwort.\n"
    "- Erfinde nichts dazu und lass nichts weg, worum nicht gebeten wurde. "
    "Hashtags, Aufgabenzeilen (- [ ]) und Medien-Marker bleiben unveraendert "
    "stehen, wenn der Auftrag sie nicht ausdruecklich betrifft.\n"
    "- Brauchst du Hintergrundwissen des Nutzers, hol es dir mit "
    "list_knowledge und read_knowledge, statt zu raten.\n"
    "- Rufe Werkzeuge ueber die Tool-Schnittstelle auf, niemals als Text.\n"
    "- Antworte auf Deutsch, in ein bis zwei Saetzen, und sag was du geaendert "
    "hast. Wiederhol den Text nicht — der Nutzer sieht ihn selbst.\n"
    "Heutiges Datum: {today}."
)

_GOAL_PROMPT = (
    "\n\nSchreibziel „{label}“ — daran haelst du dich:\n{prompt}\n"
    "Setz das Ergebnis mit set_text um: {mode_hint}"
)
_MODE_HINTS = {
    "replace": "Es ersetzt den bisherigen Text.",
    "append": "Es kommt an den bisherigen Text hinten dran, der bleibt stehen.",
}

_KNOWLEDGE_PROMPT = (
    "\n\nDer Nutzer hat diese Wissens-Hashtags ausgewaehlt: {tags}. "
    "Lies sie mit read_knowledge, bevor du den Text anfasst."
)


class WriteAgentError(agent_loop.AgentError):
    """Fehler, den die Oberflaeche dem Nutzer wortwoertlich zeigen darf."""


# ─── Schreibziele ─────────────────────────────────────────────────────
def _config():
    try:
        return _core.load_config() or {}
    except (OSError, ValueError, RuntimeError):
        return {}


def goal_options():
    """Die Schreibziele fuer die Auswahl im Fenster — ohne den Systemtext.

    Der Prompt eines Ziels ist Betreiberkonfiguration und hat im Browser nichts
    zu suchen; das Fenster nennt nur eine ID.
    """
    options = []
    for item in _config().get("ai_functions") or []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        options.append({
            "id": str(item["id"]),
            "label": str(item.get("label") or item["id"]),
            "mode": str(item.get("mode") or "append"),
        })
    return options


def resolve_goal(goal_id):
    """Das volle Schreibziel — nur serverseitig. Unbekannt heisst: kein Ziel."""
    wanted = str(goal_id or "")
    if not wanted:
        return None
    for item in _config().get("ai_functions") or []:
        if isinstance(item, dict) and str(item.get("id") or "") == wanted:
            return item
    return None


# ─── Werkzeuge ────────────────────────────────────────────────────────
TOOLS = [
    {
        "name": "get_text",
        "description": "Den Text lesen, an dem gearbeitet wird. Immer zuerst aufrufen.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "set_text",
        "description": "Den ueberarbeiteten Text zurueckgeben — vollstaendig, nicht nur die "
                       "geaenderte Stelle. Das ist der einzige Weg, eine Aenderung "
                       "vorzuschlagen; uebernommen wird sie erst vom Nutzer.",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "Der vollstaendige neue Text"}},
            "required": ["text"],
        },
    },
    agent_knowledge.LIST_TOOL,
    agent_knowledge.READ_TOOL,
]

WRITING_TOOLS = frozenset({"set_text"})


def tool_specs():
    """Die Werkzeuge in OpenAI-Function-Form."""
    return [
        {"type": "function", "function": {
            "name": tool["name"], "description": tool["description"],
            "parameters": tool["inputSchema"],
        }}
        for tool in TOOLS
    ]


def clean_text(raw, *, what="Der Text"):
    text = str(raw or "")
    if len(text) > MAX_TEXT_CHARS:
        raise mcp_tools.ToolInputError(
            "{} ist laenger als {} Zeichen.".format(what, MAX_TEXT_CHARS))
    return text


def clean_tags(raw):
    """Die im Fenster gewaehlten Wissens-Hashtags, entschaerft und gedeckelt."""
    if not isinstance(raw, list):
        return []
    tags, seen = [], set()
    for value in raw:
        tag = str(value or "").lstrip("#").strip()
        if not tag or tag in seen:
            continue
        seen.add(tag)
        tags.append(tag)
        if len(tags) >= MAX_SELECTED_TAGS:
            break
    return tags


# ─── Anbieterauswahl ──────────────────────────────────────────────────
# Dieselbe Auswahl wie beim Einkaufslisten-Agenten: zwei Agenten, die sich
# verschieden entscheiden, waeren fuer den Nutzer nicht erklaerbar.
CHAT_PROVIDER_ID = shopping_agent.CHAT_PROVIDER_ID
provider_options = shopping_agent.provider_options
default_provider_id = shopping_agent.default_provider_id
resolve_settings = shopping_agent.resolve_settings


def normalise_history(raw):
    return agent_loop.normalise_history(raw, WriteAgentError)


# ─── Profil und Lauf ──────────────────────────────────────────────────
class _WriteProfile(agent_loop.Profile):
    """Genau ein Text in der Sitzung genau eines Benutzers.

    Der Text steckt im Profil, nie in den Argumenten des Modells — dasselbe
    Prinzip wie die festgenagelte Projekt-ID beim Einkaufslisten-Agenten. Ein
    Modell kann damit ``set_text`` aufrufen, aber nicht bestimmen, *welchen*
    Text es trifft.
    """

    error_class = WriteAgentError
    recoverable_errors = (mcp_tools.ToolInputError, mcp_tools.ToolUnavailable)
    unconfigured_message = (
        "Es ist noch keine KI-Verbindung konfiguriert. "
        "Burger-Menü → AI-Worker → Anbieter."
    )

    def __init__(self, user_id, text, *, goal=None, tags=()):
        self.user_id = user_id
        self.original = text
        self.text = text
        self.goal = goal
        self.tags = list(tags)
        self.wrote = False

    def system_prompt(self):
        today = _core.get_tz_aware_now()[0].strftime("%Y-%m-%d")
        prompt = SYSTEM_PROMPT.format(today=today)
        if self.goal:
            mode = str(self.goal.get("mode") or "append")
            prompt += _GOAL_PROMPT.format(
                label=str(self.goal.get("label") or self.goal.get("id") or ""),
                prompt=str(self.goal.get("system_prompt") or "").strip(),
                mode_hint=_MODE_HINTS.get(mode, _MODE_HINTS["append"]),
            )
        if self.tags:
            prompt += _KNOWLEDGE_PROMPT.format(
                tags=", ".join("#{}".format(tag) for tag in self.tags))
        return prompt

    def tool_specs(self):
        return tool_specs()

    def run_tool(self, name, arguments):
        if not isinstance(arguments, dict):
            raise mcp_tools.ToolInputError("arguments muss ein Objekt sein")
        if name == "get_text":
            return {"text": self.text, "zeichen": len(self.text)}
        if name == "set_text":
            new_text = clean_text(arguments.get("text"), what="Der neue Text")
            if not new_text.strip():
                raise mcp_tools.ToolInputError(
                    "Ein leerer Text ist kein Vorschlag. Gib den vollstaendigen Text zurueck.")
            self.text = new_text
            self.wrote = True
            return {"uebernommen": True, "zeichen": len(new_text)}
        if name == "list_knowledge":
            return agent_knowledge.listing(self.user_id, self.tags)
        if name == "read_knowledge":
            return agent_knowledge.read(self.user_id, arguments.get("hashtag"))
        raise mcp_tools.ToolInputError("Unbekanntes Werkzeug: {}".format(name))

    def step_summary(self, name, payload):
        step = {"tool": name}
        if not isinstance(payload, dict):
            return step
        if "zeichen" in payload:
            step["zeichen"] = payload["zeichen"]
        hits = agent_knowledge.step_hits(name, payload)
        if hits is not None:
            step["hits"] = hits
        return step

    def post(self, settings, body):
        return _post(settings, body)


def _post(settings, body):
    """Der Endpunktaufruf dieses Agenten — auch der Punkt, den Tests ersetzen."""
    return agent_loop.post(settings, body, WriteAgentError)


def answer(user_id, text, history, settings, *, goal=None, tags=(), on_step=None):
    """Die Werkzeugschleife auf einem Text ausfuehren."""
    profile = _WriteProfile(user_id, text, goal=goal, tags=tags)
    result = agent_loop.run(profile, history, settings, on_step=on_step)
    changed = profile.wrote and profile.text != profile.original
    return {**result, "text": profile.text if changed else None, "changed": changed}


def ask_handler(handle, params):
    settings = resolve_settings(handle.user_id, params.get("provider_id"))
    try:
        history = normalise_history(params.get("messages"))
        text = clean_text(params.get("text"))
    except (WriteAgentError, mcp_tools.ToolInputError) as exc:
        raise background_runs.RunError(str(exc)) from exc

    goal = resolve_goal(params.get("goal_id"))
    tags = clean_tags(params.get("knowledge"))
    counter = {"steps": 0}

    def on_step(step):
        counter["steps"] += 1
        handle.log(step)
        handle.progress(state="working", tool_calls=counter["steps"])

    handle.progress(state="working", tool_calls=0)
    try:
        return answer(handle.user_id, text, history, settings,
                      goal=goal, tags=tags, on_step=on_step)
    except WriteAgentError as exc:
        raise background_runs.RunError(str(exc)) from exc


background_runs.register(RUN_KIND, ask_handler)


__all__ = [
    "CHAT_PROVIDER_ID", "MAX_ROUNDS", "MAX_SELECTED_TAGS", "MAX_TEXT_CHARS",
    "RUN_KIND", "SYSTEM_PROMPT", "TOOLS", "WriteAgentError", "answer",
    "ask_handler", "clean_tags", "clean_text", "default_provider_id",
    "goal_options", "normalise_history", "provider_options", "resolve_goal",
    "resolve_settings", "tool_specs",
]
