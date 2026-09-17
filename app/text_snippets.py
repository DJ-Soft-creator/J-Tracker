"""Validierung und Persistenz der Text-Bausteine ("Abkürzungen") in config.json.

AI maintenance contract:
Own the ``text_snippets`` array in config.json: defaults, validation and the
write path. Keep HTTP handling in main_core_routes.py. Mirrors
journal_templates.py's shape deliberately - same storage primitives, same
"replace the whole list" write contract - but snippets are a lightweight
typing aid (no admin gate, no protected ids): any signed-in user may add,
rename or remove one.
"""

import json
import uuid

from scheduling import read_text_file, update_text_file

_TRIGGER_MAX = 40
_EXPANSION_MAX = 500
_MAX_SNIPPETS = 200

# Ausgeliefert, solange config.json noch keinen eigenen Bestand hat. Das
# Minus ist bewusst dabei: auf dem Handy ist "- [ ] " ohne Sonderzeichen-
# Tastatur der eigentliche Auslöser für dieses Feature.
DEFAULT_SNIPPETS = [
    {"id": "checkbox-dash", "trigger": "-", "expansion": "- [ ] "},
    {"id": "checkbox-aufgabe", "trigger": "aufgabe", "expansion": "- [ ] "},
]


class SnippetError(ValueError):
    """Ein Textbaustein verletzt den Vertrag; die Meldung geht direkt an die UI."""


def load_snippets(config_path):
    """Rohliste aus der Datei, oder die Defaults, solange nichts gespeichert ist."""
    raw = read_text_file(config_path)
    if not (raw or "").strip():
        return list(DEFAULT_SNIPPETS)
    try:
        config = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SnippetError(f"config.json ist kein gültiges JSON: {exc}") from exc
    snippets = config.get("text_snippets")
    if not isinstance(snippets, list):
        return list(DEFAULT_SNIPPETS)
    return snippets


def save_snippets(config_path, snippets):
    """Ersetzt ausschliesslich den ``text_snippets``-Schlüssel, atomar und gesperrt."""
    def updater(current):
        if not (current or "").strip():
            raise SnippetError("config.json fehlt oder ist leer.")
        try:
            config = json.loads(current)
        except json.JSONDecodeError as exc:
            raise SnippetError(f"config.json ist kein gültiges JSON: {exc}") from exc
        config["text_snippets"] = snippets
        return json.dumps(config, ensure_ascii=False, indent=2) + "\n"

    update_text_file(config_path, updater)
    return snippets


def validate(payload):
    """Wirft ``SnippetError`` mit einer UI-tauglichen Meldung bei Verstößen."""
    if not isinstance(payload, list):
        raise SnippetError("Textbausteine (Liste) erforderlich.")
    if len(payload) > _MAX_SNIPPETS:
        raise SnippetError(f"Maximal {_MAX_SNIPPETS} Textbausteine erlaubt.")
    seen_triggers = set()
    out = []
    for item in payload:
        if not isinstance(item, dict):
            raise SnippetError("Ungültiger Textbaustein.")
        trigger = str(item.get("trigger") or "").strip()
        expansion = item.get("expansion")
        if not isinstance(expansion, str):
            expansion = ""
        if not trigger:
            raise SnippetError("Ein Auslöser darf nicht leer sein.")
        if len(trigger) > _TRIGGER_MAX:
            raise SnippetError(f"Auslöser „{trigger}“ ist länger als {_TRIGGER_MAX} Zeichen.")
        if "\n" in trigger or " " in trigger:
            raise SnippetError(f"Auslöser „{trigger}“ darf kein Leerzeichen/Umbruch enthalten.")
        if len(expansion) > _EXPANSION_MAX:
            raise SnippetError(f"Textbaustein für „{trigger}“ ist länger als {_EXPANSION_MAX} Zeichen.")
        key = trigger.casefold()
        if key in seen_triggers:
            raise SnippetError(f"Auslöser „{trigger}“ ist doppelt vergeben.")
        seen_triggers.add(key)
        item_id = item.get("id")
        if not isinstance(item_id, str) or not item_id.strip():
            item_id = str(uuid.uuid4())
        out.append({"id": item_id, "trigger": trigger, "expansion": expansion})
    return out
