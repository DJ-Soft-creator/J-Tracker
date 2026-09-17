"""Validierung und Persistenz der Journal-Templates aus ``config.json``.

AI maintenance contract:
Own the ``templates`` array in config.json: validation, canonical shape and the
write path. Keep HTTP handling in ``main_core_routes.py`` and the submit-time
semantics in ``main_journal_routes.py``.

Two invariants make this module necessary instead of a plain ``json.dump``:

1. ``main.load_config()`` returns an **env-injected** view — API keys from
   ``AI_KEY_*`` and resolved ``{locale_LLM_IP}`` placeholders. Writing that view
   back would bake secrets into config.json. Every save here re-reads the raw
   file under the shared lock and replaces the single ``templates`` key, so
   ``ai_providers``/``ai_functions`` stay untouched byte for byte in meaning.
2. A handful of template IDs are wired into code (see ``PROTECTED``). Renaming,
   retyping or deleting them silently breaks the Einkaufsliste import, the
   Planner, the Projekt-Anlage or the Schnell-Notiz. They are guarded here.

Unknown keys are intentionally dropped: the canonical shape below is what the
app actually consumes. New template features must be added to this module.
"""

import json
import re

from scheduling import read_text_file, update_text_file

TEMPLATE_TYPES = ("simple", "form")
FIELD_TYPES = ("text", "textarea", "number", "date", "select", "user")

# IDs, die im Code fest verdrahtet sind. Quelle je Eintrag:
#   schnell        main.py:813, main_journal_routes.py:100 (Schnellnotiz-Stil)
#   aufgabe        main_journal_routes.py:344 (Projektzuordnung mit Titelzeile)
#   aufgabenplaner main_journal_routes.py:187 (Planner-Eintrag)
#   projekt        main_journal_routes.py:203 (eigenstaendige Projektdatei)
#   einkaufsliste  main_journal_routes.py:155, family.py:162 (Listenimport)
# Vorlagen, deren Journal-Form in main.py._TEMPLATE_FORMATTERS verdrahtet ist:
# eine hier gepflegte ``heading`` bliebe fuer sie wirkungslos.
CODE_FORMATTED_IDS = ("aufgabe", "nebengedanke", "thema", "brainablage", "idee", "erkenntnis")

PROTECTED = {
    "schnell": (),
    "aufgabe": (),
    "aufgabenplaner": ("titel", "user", "recurrence", "target_date"),
    "projekt": ("titel", "verantwortlich"),
    "einkaufsliste": (),
}

_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_FIELD_ID_PATTERN = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]{0,63}$")
_TARGET_FILE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,79}\.md$")
_MAX_TEMPLATES = 100
_MAX_FIELDS = 40
_MAX_OPTIONS = 60


class TemplateError(ValueError):
    """Eine Vorlage verletzt den Vertrag; die Meldung geht direkt an die UI."""


def load_templates(config_path):
    """Rohliste aus der Datei — ohne die Env-Injektion aus ``load_config()``."""
    raw = read_text_file(config_path)
    if not (raw or "").strip():
        return []
    try:
        config = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise TemplateError(f"config.json ist kein gültiges JSON: {exc}") from exc
    templates = config.get("templates")
    return list(templates) if isinstance(templates, list) else []


def save_templates(config_path, templates):
    """Ersetzt ausschliesslich den ``templates``-Schlüssel, atomar und gesperrt."""
    def updater(current):
        if not (current or "").strip():
            raise TemplateError("config.json fehlt oder ist leer.")
        try:
            config = json.loads(current)
        except json.JSONDecodeError as exc:
            raise TemplateError(f"config.json ist kein gültiges JSON: {exc}") from exc
        config["templates"] = templates
        return json.dumps(config, ensure_ascii=False, indent=2) + "\n"

    update_text_file(config_path, updater)
    return templates


# ─── Validierung ──────────────────────────────────────────────────────

def _text(value, label, *, maximum, required=True):
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise TemplateError(f"{label} muss Text sein.")
    value = value.strip()
    if required and not value:
        raise TemplateError(f"{label} darf nicht leer sein.")
    if len(value) > maximum:
        raise TemplateError(f"{label} ist länger als {maximum} Zeichen.")
    return value


def _flag(value, label):
    if value is None:
        return None
    if not isinstance(value, bool):
        raise TemplateError(f"{label} muss true oder false sein.")
    return value


def _validate_option(raw, where):
    if not isinstance(raw, dict):
        raise TemplateError(f"{where}: Auswahlwerte müssen Objekte sein.")
    value = _text(raw.get("value"), f"{where}: Wert einer Auswahl", maximum=120)
    label = _text(raw.get("label"), f"{where}: Beschriftung einer Auswahl", maximum=120, required=False)
    return {"value": value, "label": label or value}


def _validate_field(raw, where, seen_ids):
    if not isinstance(raw, dict):
        raise TemplateError(f"{where}: Felder müssen Objekte sein.")
    field_id = _text(raw.get("id"), f"{where}: Feld-ID", maximum=64)
    if not _FIELD_ID_PATTERN.match(field_id):
        raise TemplateError(
            f"{where}: Feld-ID „{field_id}“ ist ungültig — erlaubt sind Buchstaben, Ziffern, _ und -."
        )
    if field_id in seen_ids:
        raise TemplateError(f"{where}: Feld-ID „{field_id}“ kommt doppelt vor.")
    seen_ids.add(field_id)

    field_type = raw.get("type")
    if field_type not in FIELD_TYPES:
        raise TemplateError(f"{where}: Feldtyp „{field_type}“ ist unbekannt.")

    field = {
        "id": field_id,
        "label": _text(raw.get("label"), f"{where}: Beschriftung", maximum=120, required=False),
        "type": field_type,
    }
    if _flag(raw.get("required"), f"{where}: Pflichtfeld"):
        field["required"] = True
    placeholder = _text(raw.get("placeholder"), f"{where}: Platzhalter", maximum=160, required=False)
    if placeholder:
        field["placeholder"] = placeholder

    if field_type == "number":
        for key, name in (("min", "Minimum"), ("max", "Maximum")):
            value = raw.get(key)
            if value in (None, ""):
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TemplateError(f"{where}: {name} muss eine Zahl sein.")
            field[key] = value
        if "min" in field and "max" in field and field["min"] > field["max"]:
            raise TemplateError(f"{where}: Minimum ist größer als Maximum.")
    elif field_type == "select":
        options = raw.get("options")
        if not isinstance(options, list) or not options:
            raise TemplateError(f"{where}: Ein Auswahlfeld braucht mindestens einen Wert.")
        if len(options) > _MAX_OPTIONS:
            raise TemplateError(f"{where}: höchstens {_MAX_OPTIONS} Auswahlwerte.")
        field["options"] = [_validate_option(option, where) for option in options]
    return field


def _validate_template(raw, seen_ids, known_user_ids, previous_by_id):
    if not isinstance(raw, dict):
        raise TemplateError("Jede Vorlage muss ein Objekt sein.")
    template_id = _text(raw.get("id"), "Die Template-ID", maximum=64)
    if not _ID_PATTERN.match(template_id):
        raise TemplateError(
            f"Die ID „{template_id}“ ist ungültig — erlaubt sind Kleinbuchstaben, Ziffern, _ und -."
        )
    if template_id in seen_ids:
        raise TemplateError(f"Die ID „{template_id}“ kommt doppelt vor.")
    seen_ids.add(template_id)
    where = f"Vorlage „{template_id}“"

    template_type = raw.get("type")
    if template_type not in TEMPLATE_TYPES:
        raise TemplateError(f"{where}: Der Typ „{template_type}“ ist unbekannt.")

    template = {
        "id": template_id,
        "label": _text(raw.get("label"), f"{where}: Die Beschriftung", maximum=120),
        "type": template_type,
        "write_to_journal": raw.get("write_to_journal", True) is not False,
    }

    heading = _text(raw.get("heading"), f"{where}: Die Journal-Überschrift", maximum=80, required=False)
    if heading:
        if template_type != "simple":
            raise TemplateError(f"{where}: Eine Überschrift gibt es nur für einfache Vorlagen.")
        if "\n" in heading or "#" in heading:
            raise TemplateError(f"{where}: Die Überschrift darf kein # und keinen Zeilenumbruch enthalten.")
        template["heading"] = heading

    if _flag(raw.get("hashtag_template"), f"{where}: Hashtag-Vorlage"):
        hashtags = raw.get("hashtags")
        if not isinstance(hashtags, list):
            raise TemplateError(f"{where}: Hashtags müssen eine Liste sein.")
        template["hashtag_template"] = True
        template["hashtags"] = [
            _text(tag, f"{where}: Ein Hashtag", maximum=64) for tag in hashtags
        ]
    if _flag(raw.get("project_selector"), f"{where}: Projektauswahl"):
        template["project_selector"] = True
    if _flag(raw.get("keep_template_after_submit"), f"{where}: Vorlage nach dem Senden behalten"):
        template["keep_template_after_submit"] = True

    assigned = raw.get("assigned_users") or []
    if not isinstance(assigned, list):
        raise TemplateError(f"{where}: Die Zuweisung muss eine Liste sein.")
    unknown = [uid for uid in assigned if uid not in known_user_ids]
    if unknown:
        raise TemplateError(f"{where}: Unbekannte Benutzer in der Zuweisung: {', '.join(map(str, unknown))}.")
    template["assigned_users"] = list(dict.fromkeys(assigned))

    target_file = raw.get("target_file")
    if target_file in (None, ""):
        template["target_file"] = None
    else:
        target_file = _text(target_file, f"{where}: Die Ziel-Datei", maximum=80)
        if not _TARGET_FILE_PATTERN.match(target_file):
            raise TemplateError(
                f"{where}: Die Ziel-Datei muss ein einfacher Dateiname mit Endung .md sein."
            )
        template["target_file"] = target_file

    if template_type == "form":
        fields = raw.get("fields")
        if not isinstance(fields, list) or not fields:
            raise TemplateError(f"{where}: Ein Formular braucht mindestens ein Feld.")
        if len(fields) > _MAX_FIELDS:
            raise TemplateError(f"{where}: höchstens {_MAX_FIELDS} Felder.")
        seen_field_ids = set()
        template["fields"] = [_validate_field(field, where, seen_field_ids) for field in fields]

    _guard_protected(template, previous_by_id.get(template_id), where)
    return template


def _guard_protected(template, previous, where):
    """Systemvorlagen behalten ihre im Code verdrahteten Merkmale."""
    template_id = template["id"]
    if template_id not in PROTECTED:
        return
    if previous is None:
        return  # Neuanlage unter einem Systemnamen: Struktur wird unten geprueft.
    if previous.get("type") != template["type"]:
        raise TemplateError(f"{where} ist eine Systemvorlage — der Typ kann nicht geändert werden.")
    if (previous.get("target_file") or None) != template["target_file"]:
        raise TemplateError(f"{where} ist eine Systemvorlage — die Ziel-Datei kann nicht geändert werden.")


def validate(payload, known_user_ids, previous_templates):
    """Prüft die komplette Liste und gibt sie in kanonischer Form zurück."""
    if not isinstance(payload, list):
        raise TemplateError("templates muss eine Liste sein.")
    if not payload:
        raise TemplateError("Es muss mindestens eine Vorlage geben.")
    if len(payload) > _MAX_TEMPLATES:
        raise TemplateError(f"Höchstens {_MAX_TEMPLATES} Vorlagen.")

    previous_by_id = {
        item.get("id"): item for item in previous_templates if isinstance(item, dict)
    }
    seen_ids = set()
    known = set(known_user_ids)
    templates = [
        _validate_template(raw, seen_ids, known, previous_by_id) for raw in payload
    ]

    removed = [
        template_id for template_id in PROTECTED
        if template_id in previous_by_id and template_id not in seen_ids
    ]
    if removed:
        raise TemplateError(
            "Diese Systemvorlagen sind im Code verdrahtet und können nicht gelöscht werden: "
            + ", ".join(sorted(removed))
        )

    by_id = {template["id"]: template for template in templates}
    for template_id, required_fields in PROTECTED.items():
        template = by_id.get(template_id)
        if not template or not required_fields:
            continue
        present = {field["id"] for field in template.get("fields", [])}
        missing = [field_id for field_id in required_fields if field_id not in present]
        if missing:
            raise TemplateError(
                f"Vorlage „{template_id}“ ist eine Systemvorlage und braucht die Felder: "
                + ", ".join(missing)
            )
    return templates
