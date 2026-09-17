"""Der Einkaufslisten-Agent: Werkzeuge auf genau einer Familienliste.

AI maintenance contract: dieses Modul haelt die Werkzeuge und den
serverseitigen Lauf. Die Werkzeugschleife selbst steht in agent_loop.py, die
HTTP-Validierung in shopping_agent_routes.py, die Schreibprimitive in
family.py.

Der Agent ist bewusst eng gefasst: die Projekt-ID steckt im Profil, nie in den
Argumenten des Modells. Ein Modell kann damit ein Werkzeug benennen, aber
niemals seinen Wirkungsbereich — genauso wie der Notizen-Chat ``sources`` fest
auf ``personal`` setzt. Jedes Werkzeug laedt die Liste frisch und prueft die
Sichtbarkeit erneut, weil eine Familienliste sich waehrend eines Laufs von
anderer Seite aendern kann.

Artikel werden ueber 1-basierte Indizes angesprochen, nie ueber ihren Namen.
Das ist derselbe Kniff, mit dem die alte Sortierfunktion verhinderte, dass ein
Modell Artikel stillschweigend umbenennt oder erfindet. Weil add/remove die
Nummerierung verschieben, liefert jedes schreibende Werkzeug die neue Liste
gleich mit zurueck.
"""

import json
import logging
import sys
import time
import urllib.parse
import uuid

import agent_knowledge
import agent_loop
import ai_providers
import background_runs
import brain_chat
import family as family_module
import mcp_tools
from scheduling import read_text_file, write_text_file

logger = logging.getLogger(__name__)

_core = sys.modules.get("main") or sys.modules.get("__main__")
if _core is None:  # pragma: no cover - import order is a hard application invariant
    raise RuntimeError("main facade must exist before shopping_agent is imported")

MAX_ROUNDS = agent_loop.MAX_ROUNDS
UNDO_STORE = "shopping-agent-undo.json"
# Rueckgaengig ist fuer den Griff direkt nach dem Lauf gedacht, nicht fuer das
# Zurueckrollen eines Tages: eine Familienliste gehoert mehreren, und ein
# spaeter Rollback wuerde fremde Eintraege mitnehmen.
UNDO_MAX_AGE_SECONDS = 3600
MAX_ITEMS = 300
MAX_TITLE_CHARS = 160
MAX_NEW_ITEMS_PER_CALL = 50

SYSTEM_PROMPT = (
    "Du bist der Einkaufslisten-Assistent von Journl und arbeitest an genau "
    "einer Einkaufsliste: „{list_title}“.\n\n"
    "Regeln:\n"
    "- Sieh dir die Liste immer zuerst mit list_items an.\n"
    "- Artikel sprichst du ausschliesslich ueber ihre Nummer an, nie ueber "
    "ihren Namen.\n"
    "- Beim Sortieren nach einem Laden: hol dir den Ladenaufbau mit "
    "read_knowledge und uebernimm dessen Rubriken in genau dessen Reihenfolge. "
    "Erfinde keine eigenen Rubriken, wenn eine Ladennotiz vorliegt.\n"
    "- Ist kein Laden genannt, sortiere nach den ueblichen Rubriken: "
    "Obst & Gemuese, Kuehlung, Tiefkuehlung, Konserven, Trockenwaren, "
    "Getraenke, Suessigkeiten, Non-Food, Sonstiges.\n"
    "- Fuege nie ungefragt Artikel hinzu und loesche nie ungefragt welche.\n"
    "- Rufe Werkzeuge ueber die Tool-Schnittstelle auf, niemals als Text.\n"
    "- Antworte auf Deutsch, in ein bis zwei Saetzen, und sag was du getan "
    "hast. Zaehl nicht die ganze Liste auf — der Nutzer sieht sie selbst.\n"
    "Heutiges Datum: {today}."
)


class ShoppingAgentError(agent_loop.AgentError):
    """Fehler, den die Oberflaeche dem Nutzer wortwoertlich zeigen darf."""


# ─── Zugriff auf die eine Liste ───────────────────────────────────────
def _load_visible(user_id, project_id):
    """Die Liste frisch lesen und Sichtbarkeit sowie Typ erneut pruefen."""
    project = family_module._load_project(project_id)
    if not project or not family_module._project_visible(project, user_id):
        raise LookupError("Not found")
    if not family_module._is_shopping_project(project):
        raise ValueError("Das ist keine Einkaufsliste.")
    return project


def _items_of(project):
    """Die vom Agenten anfassbaren Artikel — wiederkehrende bleiben aussen vor.

    Wiederkehrende Aufgaben gehoeren dem Planer. Wuerde der Agent sie
    umsortieren oder loeschen, waere der naechste Scheduler-Lauf die
    Ueberraschung.
    """
    return [t for t in project.get("tasks", []) if t.get("source") != "recurring"
            and not t.get("recurring")]


def _recurring_of(project):
    return [t for t in project.get("tasks", []) if t.get("source") == "recurring"
            or t.get("recurring")]


def _numbered(project):
    """Der Listenstand, wie das Modell ihn sieht: 1-basiert mit Rubrik."""
    items = []
    for number, task in enumerate(_items_of(project), 1):
        items.append({
            "nummer": number,
            "titel": task.get("title", ""),
            "rubrik": task.get("gruppe", "") or "",
            "abgehakt": bool(task.get("completed")),
        })
    return items


def _state(project, **extra):
    return {"liste": project.get("title", ""), "artikel": _numbered(project), **extra}


def _pick(project, numbers, what="items"):
    """1-basierte Nummern auf Aufgaben abbilden, streng und ohne Dubletten."""
    if not isinstance(numbers, list) or not numbers:
        raise mcp_tools.ToolInputError("{} muss eine nicht-leere Liste von Nummern sein".format(what))
    items = _items_of(project)
    picked, seen = [], set()
    for value in numbers:
        if isinstance(value, bool) or not isinstance(value, int):
            raise mcp_tools.ToolInputError("'{}' ist keine Artikelnummer".format(value))
        if value < 1 or value > len(items):
            raise mcp_tools.ToolInputError(
                "Es gibt keinen Artikel {}. Die Liste hat {} Artikel.".format(value, len(items)))
        if value in seen:
            continue
        seen.add(value)
        picked.append(items[value - 1])
    return picked


def _clean_title(raw):
    title = str(raw or "").strip()
    if not title:
        raise mcp_tools.ToolInputError("Ein Artikel braucht einen Titel")
    if len(title) > MAX_TITLE_CHARS:
        raise mcp_tools.ToolInputError("Der Titel ist zu lang (max. {} Zeichen)".format(MAX_TITLE_CHARS))
    if "\n" in title or "|" in title:
        # '|' trennt die Maschinenfelder einer Aufgabenzeile im Markdown.
        raise mcp_tools.ToolInputError("Ein Titel darf keine Zeilenumbrueche oder '|' enthalten")
    return title


def _clean_group(raw):
    group = str(raw or "").strip().lstrip("#").strip()
    if len(group) > 80 or "\n" in group or "|" in group:
        raise mcp_tools.ToolInputError("Der Rubrikname ist ungueltig")
    return group


# ─── Werkzeuge ────────────────────────────────────────────────────────
TOOLS = [
    {
        "name": "list_items",
        "description": "Die aktuelle Einkaufsliste mit Nummern, Rubriken und Haekchen lesen. "
                       "Immer zuerst aufrufen.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "reorder_items",
        "description": "Die ganze Liste neu ordnen und in Rubriken einteilen. Die Rubriken "
                       "kommen in der angegebenen Reihenfolge, die Artikel innerhalb einer "
                       "Rubrik ebenso. Nicht genannte Artikel landen am Ende unter "
                       "'Unsortiert' und gehen nie verloren.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "rubriken": {
                    "type": "array", "minItems": 1,
                    "description": "Die Rubriken in der Reihenfolge, in der sie im Laden liegen.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "Name der Rubrik"},
                            "artikel": {
                                "type": "array", "items": {"type": "integer"},
                                "description": "Artikelnummern in dieser Rubrik, in Laufreihenfolge",
                            },
                        },
                        "required": ["name", "artikel"],
                    },
                },
            },
            "required": ["rubriken"],
        },
    },
    {
        "name": "add_items",
        "description": "Neue Artikel an die Liste anhaengen. Nur auf ausdruecklichen Wunsch.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "titel": {"type": "array", "items": {"type": "string"}, "minItems": 1,
                          "description": "Die neuen Artikel, einer je Eintrag"},
                "rubrik": {"type": "string", "description": "Optionale Rubrik fuer alle neuen Artikel"},
            },
            "required": ["titel"],
        },
    },
    {
        "name": "remove_items",
        "description": "Artikel endgueltig von der Liste entfernen. Nur auf ausdruecklichen Wunsch.",
        "inputSchema": {
            "type": "object",
            "properties": {"artikel": {"type": "array", "items": {"type": "integer"}, "minItems": 1,
                                       "description": "Artikelnummern"}},
            "required": ["artikel"],
        },
    },
    {
        "name": "rename_item",
        "description": "Einen Artikel umbenennen, etwa um eine Menge zu ergaenzen oder zwei "
                       "doppelte Eintraege zu einem zusammenzufassen.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "artikel": {"type": "integer", "description": "Artikelnummer"},
                "titel": {"type": "string", "description": "Der neue Titel"},
            },
            "required": ["artikel", "titel"],
        },
    },
    {
        "name": "set_completed",
        "description": "Artikel abhaken oder das Haekchen wieder entfernen.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "artikel": {"type": "array", "items": {"type": "integer"}, "minItems": 1,
                            "description": "Artikelnummern"},
                "abgehakt": {"type": "boolean", "description": "true hakt ab, false nimmt das Haekchen weg"},
            },
            "required": ["artikel", "abgehakt"],
        },
    },
    agent_knowledge.LIST_TOOL,
    agent_knowledge.READ_TOOL,
]


def tool_specs():
    """Die Werkzeuge in OpenAI-Function-Form."""
    return [
        {"type": "function", "function": {
            "name": tool["name"], "description": tool["description"],
            "parameters": tool["inputSchema"],
        }}
        for tool in TOOLS
    ]


def _tool_list_items(user_id, project_id, arguments):
    return _state(_load_visible(user_id, project_id))


def _tool_reorder_items(user_id, project_id, arguments):
    project = _load_visible(user_id, project_id)
    groups = arguments.get("rubriken")
    if not isinstance(groups, list) or not groups:
        raise mcp_tools.ToolInputError("rubriken muss eine nicht-leere Liste sein")
    ordered, used = [], set()
    position = 0
    for group in groups:
        if not isinstance(group, dict):
            raise mcp_tools.ToolInputError("Jede Rubrik muss ein Objekt mit name und artikel sein")
        name = _clean_group(group.get("name"))
        for task in _pick(project, group.get("artikel"), "artikel"):
            if id(task) in used:
                continue
            used.add(id(task))
            position += 1
            task["sort_index"] = position
            if name:
                task["gruppe"] = name
            else:
                task.pop("gruppe", None)
            ordered.append(task)
    # Was das Modell uebersehen hat, haengt hinten an. Ein vergessener Artikel
    # darf niemals ein geloeschter Artikel sein.
    forgotten = 0
    for task in _items_of(project):
        if id(task) not in used:
            position += 1
            task["sort_index"] = position
            task["gruppe"] = "Unsortiert"
            ordered.append(task)
            forgotten += 1
    project["tasks"] = _recurring_of(project) + ordered
    family_module._save_project(project)
    return _state(project, sortiert=len(used), nachgetragen=forgotten)


def _tool_add_items(user_id, project_id, arguments):
    project = _load_visible(user_id, project_id)
    titles = arguments.get("titel")
    if not isinstance(titles, list) or not titles:
        raise mcp_tools.ToolInputError("titel muss eine nicht-leere Liste sein")
    if len(titles) > MAX_NEW_ITEMS_PER_CALL:
        raise mcp_tools.ToolInputError(
            "Hoechstens {} Artikel auf einmal".format(MAX_NEW_ITEMS_PER_CALL))
    cleaned = [_clean_title(title) for title in titles]
    group = _clean_group(arguments.get("rubrik"))
    existing = _items_of(project)
    if len(existing) + len(cleaned) > MAX_ITEMS:
        raise mcp_tools.ToolInputError("Die Liste waere danach laenger als {} Artikel".format(MAX_ITEMS))
    now, _unused = family_module._get_tz_aware_now()
    added = []
    for title in cleaned:
        task = {
            "id": str(uuid.uuid4()), "title": title, "user": "", "target_date": "",
            "completed": False, "created_at": now.isoformat(), "created_by": user_id,
            "completed_at": None, "completed_by": None,
        }
        if group:
            task["gruppe"] = group
        added.append(task)
    project["tasks"] = _recurring_of(project) + existing + added
    family_module._save_project(project)
    return _state(project, hinzugefuegt=len(added))


def _tool_remove_items(user_id, project_id, arguments):
    project = _load_visible(user_id, project_id)
    doomed = _pick(project, arguments.get("artikel"), "artikel")
    doomed_ids = {id(task) for task in doomed}
    titles = [task.get("title", "") for task in doomed]
    project["tasks"] = _recurring_of(project) + [
        task for task in _items_of(project) if id(task) not in doomed_ids
    ]
    family_module._save_project(project)
    return _state(project, entfernt=titles)


def _tool_rename_item(user_id, project_id, arguments):
    project = _load_visible(user_id, project_id)
    number = arguments.get("artikel")
    task = _pick(project, [number], "artikel")[0]
    before = task.get("title", "")
    task["title"] = _clean_title(arguments.get("titel"))
    family_module._save_project(project)
    return _state(project, vorher=before, nachher=task["title"])


def _tool_set_completed(user_id, project_id, arguments):
    project = _load_visible(user_id, project_id)
    completed = arguments.get("abgehakt")
    if not isinstance(completed, bool):
        raise mcp_tools.ToolInputError("abgehakt muss true oder false sein")
    now, _unused = family_module._get_tz_aware_now()
    changed = 0
    for task in _pick(project, arguments.get("artikel"), "artikel"):
        if bool(task.get("completed")) == completed:
            continue
        task["completed"] = completed
        task["completed_at"] = now.isoformat() if completed else None
        task["completed_by"] = user_id if completed else None
        changed += 1
    family_module._save_project(project)
    return _state(project, geaendert=changed)


def _tool_list_knowledge(user_id, project_id, arguments):
    return agent_knowledge.listing(user_id)


def _tool_read_knowledge(user_id, project_id, arguments):
    return agent_knowledge.read(user_id, arguments.get("hashtag"))


_RUNNERS = {
    "list_items": _tool_list_items,
    "reorder_items": _tool_reorder_items,
    "add_items": _tool_add_items,
    "remove_items": _tool_remove_items,
    "rename_item": _tool_rename_item,
    "set_completed": _tool_set_completed,
    "list_knowledge": _tool_list_knowledge,
    "read_knowledge": _tool_read_knowledge,
}

WRITING_TOOLS = frozenset({"reorder_items", "add_items", "remove_items",
                           "rename_item", "set_completed"})


def run_tool(user_id, project_id, name, arguments):
    """Ein Werkzeug auf der festgenagelten Liste ausfuehren."""
    runner = _RUNNERS.get(name)
    if runner is None:
        raise mcp_tools.ToolInputError("Unbekanntes Werkzeug: {}".format(name))
    if not isinstance(arguments, dict):
        raise mcp_tools.ToolInputError("arguments muss ein Objekt sein")
    try:
        return runner(user_id, project_id, arguments)
    except LookupError as exc:
        # Nicht korrigierbar: die Liste ist weg oder nicht mehr sichtbar.
        raise ShoppingAgentError("Die Einkaufsliste ist nicht mehr erreichbar.") from exc
    except ValueError as exc:
        if isinstance(exc, mcp_tools.ToolInputError):
            raise
        raise ShoppingAgentError(str(exc)) from exc


# ─── Rueckgaengig ─────────────────────────────────────────────────────
def _undo_path(user_id):
    return _core.DATA_DIR / user_id / ".config" / UNDO_STORE


def save_undo_point(user_id, project):
    """Den Listenstand vor dem ersten Schreibzugriff eines Laufs sichern.

    Bewusst nur ein Platz je Nutzer: „Rueckgaengig" meint den Griff direkt
    nach dem Lauf, nicht eine Versionshistorie. Die hat die Liste als
    Markdown-Datei ohnehin schon.
    """
    snapshot = {
        "schema": 1, "project_id": project.get("id", ""),
        "title": project.get("title", ""), "saved_at": time.time(),
        "tasks": json.loads(json.dumps(project.get("tasks", []))),
    }
    try:
        write_text_file(_undo_path(user_id), json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n")
    except OSError:
        # Ein fehlender Rueckwaertsgang darf den Lauf nicht verhindern.
        logger.exception("Undo point could not be written for %s", user_id)
        return False
    return True


def undo_available(user_id, project_id):
    path = _undo_path(user_id)
    if not path.is_file() or path.is_symlink():
        return None
    try:
        stored = json.loads(read_text_file(path))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(stored, dict) or stored.get("project_id") != project_id:
        return None
    age = time.time() - float(stored.get("saved_at") or 0)
    if age < 0 or age > UNDO_MAX_AGE_SECONDS:
        return None
    return stored


def undo(user_id, project_id):
    """Den gesicherten Stand zurueckschreiben und den Platz danach raeumen."""
    stored = undo_available(user_id, project_id)
    if not stored:
        raise ShoppingAgentError("Es gibt keinen Stand mehr, zu dem zurueckgekehrt werden kann.")
    try:
        project = _load_visible(user_id, project_id)
    except LookupError as exc:
        raise ShoppingAgentError("Die Einkaufsliste ist nicht mehr erreichbar.") from exc
    except ValueError as exc:
        raise ShoppingAgentError(str(exc)) from exc
    project["tasks"] = stored.get("tasks", [])
    family_module._save_project(project)
    try:
        _undo_path(user_id).unlink()
    except OSError:
        logger.exception("Undo point could not be cleared for %s", user_id)
    return _state(project)


# ─── Profil und Lauf ──────────────────────────────────────────────────
class _ShoppingProfile(agent_loop.Profile):
    """Die Werkzeuge genau einer Liste in der Sitzung genau eines Benutzers."""

    error_class = ShoppingAgentError
    recoverable_errors = (mcp_tools.ToolInputError, mcp_tools.ToolUnavailable)
    unconfigured_message = (
        "Es ist noch keine KI-Verbindung konfiguriert. "
        "Brain → „…“ → Chat with your Notes → Einstellungen."
    )

    def __init__(self, user_id, project_id, list_title):
        self.user_id = user_id
        self.project_id = project_id
        self.list_title = list_title
        self.undo_saved = False
        self.wrote = False

    def system_prompt(self):
        today = _core.get_tz_aware_now()[0].strftime("%Y-%m-%d")
        return SYSTEM_PROMPT.format(list_title=self.list_title, today=today)

    def tool_specs(self):
        return tool_specs()

    def run_tool(self, name, arguments):
        if name in WRITING_TOOLS and not self.undo_saved:
            # Der Rueckwaertsgang entsteht vor dem ersten Schreibzugriff, nicht
            # beim Start: ein Lauf, der nur liest, soll einen aelteren
            # Wiederherstellungspunkt nicht wegwerfen.
            try:
                self.undo_saved = save_undo_point(self.user_id, _load_visible(self.user_id, self.project_id))
            except (LookupError, ValueError):
                self.undo_saved = False
        payload = run_tool(self.user_id, self.project_id, name, arguments)
        if name in WRITING_TOOLS:
            self.wrote = True
        return payload

    def step_summary(self, name, payload):
        step = {"tool": name}
        if not isinstance(payload, dict):
            return step
        for field in ("sortiert", "nachgetragen", "hinzugefuegt", "geaendert", "vorher", "nachher"):
            if field in payload:
                step[field] = payload[field]
        if "entfernt" in payload:
            step["entfernt"] = payload["entfernt"][:20]
        hits = agent_knowledge.step_hits(name, payload)
        if hits is not None:
            step["hits"] = hits
        if "artikel" in payload:
            step["artikel"] = len(payload["artikel"])
        return step

    def post(self, settings, body):
        return _post(settings, body)


def _post(settings, body):
    """Der Endpunktaufruf dieses Agenten — auch der Punkt, den Tests ersetzen."""
    return agent_loop.post(settings, body, ShoppingAgentError)


# ─── Anbieterauswahl ──────────────────────────────────────────────────
# Zwei Quellen, bewusst nebeneinander: die persoenliche Verbindung des
# Notizen-Chats (lokal begrenzt) und die global gepflegten ``ai_providers``
# aus der config.json. Welches Modell Werkzeuge sauber bedient, ist von
# Modell zu Modell verschieden — deshalb ist die Wahl im Fenster und nicht in
# einer Einstellungsseite vergraben.
CHAT_PROVIDER_ID = ai_providers.CHAT_PROVIDER_ID
_PRIVATE_PREFIXES = ("10.", "192.168.", "127.", "172.16.", "172.17.", "172.18.",
                     "172.19.", "172.20.", "172.21.", "172.22.", "172.23.",
                     "172.24.", "172.25.", "172.26.", "172.27.", "172.28.",
                     "172.29.", "172.30.", "172.31.")


def _is_local(url):
    host = urllib.parse.urlsplit(str(url or "")).hostname or ""
    return host in ("localhost", "::1") or host.startswith(_PRIVATE_PREFIXES)


def _providers():
    """Die brauchbaren Eintraege aus ``ai_providers``, Platzhalter aussortiert."""
    try:
        config = _core.load_config()
    except (OSError, ValueError):
        return []
    found = []
    for provider in config.get("ai_providers", []) or []:
        pid = str(provider.get("id") or "")
        url = str(provider.get("api_url") or "")
        # Ein nicht aufgeloestes {locale_LLM_IP} ist keine Adresse, sondern
        # eine fehlende Einstellung. Sie gar nicht erst anzubieten ist
        # ehrlicher als ein Fehler beim ersten Versuch.
        if not pid or not url or "{locale_LLM_IP}" in url or not provider.get("model"):
            continue
        found.append(provider)
    return found


def default_provider_id(options):
    """Der eingestellte Standard-Agent, sonst der erste brauchbare Eintrag.

    Gepflegt wird er im Burger-Menue (AI-Worker → Anbieter). Zeigt die
    Einstellung auf einen Anbieter, den es nicht mehr gibt, faellt die Wahl
    stillschweigend auf den ersten zurueck — eine leere Auswahl waere die
    schlechtere Antwort auf eine veraltete Einstellung.
    """
    try:
        wanted = ai_providers.load_default_id(_core.CONFIG_PATH)
    except (ai_providers.ProviderError, OSError):
        wanted = ""
    if wanted and any(item["id"] == wanted for item in options):
        return wanted
    return options[0]["id"] if options else ""


def provider_options(user_id):
    """Was die Oberflaeche zur Auswahl stellen darf — nie ein Schluessel."""
    options = []
    chat = brain_chat.load_settings(user_id)
    if agent_loop.configured(chat):
        options.append({"id": CHAT_PROVIDER_ID, "label": "Notizen-Chat",
                        "model": chat["model"], "local": _is_local(chat["base_url"])})
    for provider in _providers():
        options.append({
            "id": provider["id"],
            "label": str(provider.get("label") or provider.get("name") or provider["id"]),
            "model": str(provider.get("model") or ""),
            "local": _is_local(provider.get("api_url")),
        })
    return options


def resolve_settings(user_id, provider_id=""):
    """Die vollen Verbindungsdaten eines Anbieters — nur serverseitig.

    Ein leerer Wunsch nimmt den eingestellten Standard-Agenten. Ein unbekannter
    faellt ebenfalls dorthin zurueck und zuletzt auf den Notizen-Chat, damit ein
    veralteter Merker im Browser den Lauf nicht scheitern laesst.
    """
    provider_id = str(provider_id or "")
    if not provider_id:
        try:
            provider_id = ai_providers.load_default_id(_core.CONFIG_PATH)
        except (ai_providers.ProviderError, OSError):
            provider_id = ""
    if provider_id and provider_id != CHAT_PROVIDER_ID:
        for provider in _providers():
            if provider["id"] == provider_id:
                return {"url": str(provider.get("api_url") or ""), "base_url": "",
                        "model": str(provider.get("model") or ""),
                        "api_key": str(provider.get("api_key") or "")}
    return brain_chat.load_settings(user_id)


def load_settings(user_id):
    """Die Standardverbindung: der eingestellte Standard-Agent, sonst der Notizen-Chat."""
    return resolve_settings(user_id)


def normalise_history(raw):
    return agent_loop.normalise_history(raw, ShoppingAgentError)


def answer(user_id, project_id, history, settings, on_step=None):
    """Die Werkzeugschleife auf einer Liste ausfuehren."""
    try:
        project = _load_visible(user_id, project_id)
    except LookupError as exc:
        raise ShoppingAgentError("Die Einkaufsliste wurde nicht gefunden.") from exc
    except ValueError as exc:
        raise ShoppingAgentError(str(exc)) from exc
    profile = _ShoppingProfile(user_id, project_id, project.get("title", ""))
    result = agent_loop.run(profile, history, settings, on_step=on_step)
    return {**result, "changed": profile.wrote, "can_undo": profile.wrote and profile.undo_saved}


# ─── Serverseitiger Lauf ──────────────────────────────────────────────
# Wie beim Notizen-Chat: mehrere Werkzeugrunden koennen Minuten dauern. Als
# einzelner Request haenge die Antwort am offenen Browser — auf dem Handy im
# Supermarkt genau die Situation, in der er sich sperrt.
RUN_KIND = "shopping_agent"


def ask_handler(handle, params):
    settings = resolve_settings(handle.user_id, params.get("provider_id"))
    project_id = str(params.get("project_id") or "")
    try:
        history = normalise_history(params.get("messages"))
    except ShoppingAgentError as exc:
        raise background_runs.RunError(str(exc)) from exc

    counter = {"steps": 0}

    def on_step(step):
        counter["steps"] += 1
        handle.log(step)
        handle.progress(state="working", tool_calls=counter["steps"])

    handle.progress(state="working", tool_calls=0)
    try:
        return answer(handle.user_id, project_id, history, settings, on_step=on_step)
    except ShoppingAgentError as exc:
        raise background_runs.RunError(str(exc)) from exc


background_runs.register(RUN_KIND, ask_handler)


__all__ = [
    "CHAT_PROVIDER_ID", "MAX_ITEMS", "MAX_ROUNDS", "RUN_KIND", "SYSTEM_PROMPT",
    "ShoppingAgentError", "TOOLS", "answer", "ask_handler", "default_provider_id",
    "load_settings", "normalise_history", "provider_options", "resolve_settings", "run_tool",
    "save_undo_point", "tool_specs", "undo", "undo_available",
]
