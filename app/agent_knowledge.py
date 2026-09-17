"""Wissensnotizen hinter Hashtags — die gemeinsame Quelle fuer alle Agenten.

AI maintenance contract: dieses Modul kennt nur den Wissenskatalog eines
Benutzers und den Weg, eine Notiz dahinter zu lesen. Es weiss nichts darueber,
welcher Agent fragt oder was er damit vorhat.

Herausgeloest aus shopping_agent.py, als der Schreib-Agent dieselben zwei
Werkzeuge brauchte. Eine zweite Kopie waere die schlechtere Wahl gewesen: der
Lesepfad ueber ``main._knowledge_snapshots_for_write`` traegt die
Family-Sichtbarkeitspruefung und die Groessengrenze, und beides muss fuer jeden
Agenten identisch gelten. Ein zweiter Lesepfad waere ein zweites Leck.
"""

import sys

import mcp_tools
import tagging as tagging_module

_core = sys.modules.get("main") or sys.modules.get("__main__")
if _core is None:  # pragma: no cover - import order is a hard application invariant
    raise RuntimeError("main facade must exist before agent_knowledge is imported")

MAX_DESCRIPTION_CHARS = 240

# Die Werkzeugbeschreibungen liegen hier, damit zwei Agenten dem Modell nicht
# zwei verschiedene Geschichten ueber dasselbe Werkzeug erzaehlen.
LIST_TOOL = {
    "name": "list_knowledge",
    "description": "Die verfuegbaren Wissens-Hashtags auflisten — dauerhafte Notizen des "
                   "Nutzers, etwa der Aufbau eines Ladens oder ein Schreibstil. "
                   "Aufrufen, wenn ein Thema genannt wird und unklar ist, welcher "
                   "Hashtag dazu gehoert.",
    "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
}

READ_TOOL = {
    "name": "read_knowledge",
    "description": "Die Notiz hinter einem Wissens-Hashtag lesen.",
    "inputSchema": {
        "type": "object",
        "properties": {"hashtag": {"type": "string", "description": "Der Hashtag, ohne '#'"}},
        "required": ["hashtag"],
    },
}


def catalog(user_id):
    """Alle Wissens-Hashtags, die dieser Benutzer sehen darf — Tag → Eintrag."""
    view = tagging_module.catalog_view(user_id).get("knowledge", {})
    if not isinstance(view, dict):
        return {}
    found = {}
    for scope in ("personal", "family", "host"):
        for tag, value in (view.get(scope) or {}).items():
            if isinstance(value, dict):
                found.setdefault(tag, value)
    return found


def listing(user_id, selected=()):
    """Der Katalog als Werkzeugantwort.

    ``selected`` markiert die im Fenster vorgewaehlten Hashtags. Markieren statt
    filtern: das Modell soll sehen, dass es daneben noch anderes Wissen gibt,
    aber wissen, worum der Nutzer gebeten hat.
    """
    chosen = {str(tag).lstrip("#") for tag in selected}
    tags = []
    for tag, value in sorted(catalog(user_id).items()):
        entry = {"hashtag": tag,
                 "beschreibung": str(value.get("description") or "")[:MAX_DESCRIPTION_CHARS]}
        if tag in chosen:
            entry["ausgewaehlt"] = True
        tags.append(entry)
    return {"hashtags": tags}


def read(user_id, raw_hashtag):
    """Die Notiz hinter einem Hashtag, oder ein fuer das Modell korrigierbarer Fehler."""
    tag = tagging_module.normalise_tag(str(raw_hashtag or "").lstrip("#"))
    if not tag:
        raise mcp_tools.ToolInputError("hashtag fehlt")
    if tag not in catalog(user_id):
        raise mcp_tools.ToolInputError(
            "Zu #{} gibt es keine Wissensnotiz. list_knowledge zeigt, was es gibt.".format(tag))
    try:
        # Derselbe Weg wie beim Schreiben-Tab: loest auf, prueft die
        # Family-Sichtbarkeit neu und deckelt die Groesse.
        snapshots, references = _core._knowledge_snapshots_for_write(user_id, "#{}".format(tag))
    except ValueError as exc:
        raise mcp_tools.ToolInputError(str(exc)) from exc
    for snapshot in snapshots:
        if snapshot.get("tag") == tag:
            return {"hashtag": tag, "quelle": snapshot.get("path", ""),
                    "inhalt": snapshot.get("content", "")}
    if references:
        raise mcp_tools.ToolInputError(
            "#{} verweist auf einen externen Ordner, den dieser Agent nicht liest.".format(tag))
    raise mcp_tools.ToolInputError("Zu #{} liess sich keine Notiz lesen.".format(tag))


def step_hits(name, payload):
    """Der anzeigbare Abriss der beiden Werkzeuge — fuer ``Profile.step_summary``."""
    if not isinstance(payload, dict):
        return None
    if name == "list_knowledge":
        return [tag["hashtag"] for tag in (payload.get("hashtags") or [])[:20]]
    if name == "read_knowledge":
        return [payload.get("quelle", "")]
    return None


__all__ = ["LIST_TOOL", "MAX_DESCRIPTION_CHARS", "READ_TOOL", "catalog",
           "listing", "read", "step_hits"]
