"""Validierung und Persistenz der KI-Anbieter (``ai_providers``) in ``config.json``.

AI maintenance contract:
Own the ``ai_providers`` array and the ``ai_default_provider`` key in
config.json: validation, canonical shape, secret handling and the write path.
HTTP handling stays in ``agent_workbench.py``, the Auswahl im Agentenfenster in
``shopping_agent.py``/``write_agent.py``.

Dasselbe Schreibmuster wie ``journal_templates.py``, aus denselben zwei
Gruenden — und einem dritten, der nur hier gilt:

1. ``main.load_config()`` liefert eine **env-injizierte** Sicht: Schluessel aus
   ``AI_KEY_*`` und aufgeloeste ``{locale_LLM_IP}``-Platzhalter. Diese Sicht
   zurueckzuschreiben wuerde Geheimnisse in die config.json brennen und den
   Platzhalter durch eine feste IP ersetzen. Jeder Speichervorgang liest die
   Rohdatei unter der gemeinsamen Sperre neu und ersetzt nur diese Schluessel.
2. Anbieter-IDs stehen in gespeicherten Einstellungen (``historical_tagging_ai``,
   der Merker im Browser). Eine ID umzubenennen
   bricht diese Verweise still — deshalb ist die ID nach dem Anlegen fest.
3. Ein API-Schluessel darf die Oberflaeche nie erreichen. Nach aussen geht nur
   ``has_key``; beim Speichern bedeutet ein fehlendes Feld „unveraendert", ein
   leeres „loeschen".
"""

import json
import re

from scheduling import read_text_file, update_text_file

# Die Anbieterauswahl im Agentenfenster kennt neben ``ai_providers`` noch die
# persoenliche Chat-Verbindung. Sie darf als Standard gewaehlt sein, obwohl sie
# in dieser Datei nicht steht.
CHAT_PROVIDER_ID = "__chat__"

_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_PLACEHOLDER = "{locale_LLM_IP}"
_MAX_PROVIDERS = 40
_MAX_LABEL = 120
_MAX_URL = 400
_MAX_MODEL = 160
_MAX_KEY = 400
_MAX_TOKENS_LIMIT = 400000

# Reihenfolge und Umfang dessen, was die Verwaltung kennt. Unbekannte Felder
# eines Eintrags bleiben erhalten (siehe ``_merge``) — die config.json wird
# auch von Hand gepflegt, und ein hier unbekanntes Feld ist kein Muell.
FIELDS = ("id", "label", "api_url", "model", "max_tokens", "temperature")


class ProviderError(ValueError):
    """Ein Anbieter verletzt den Vertrag; die Meldung geht direkt an die UI."""


# ─── Lesen ────────────────────────────────────────────────────────────
def _raw_config(config_path):
    raw = read_text_file(config_path)
    if not (raw or "").strip():
        raise ProviderError("config.json fehlt oder ist leer.")
    try:
        config = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProviderError(f"config.json ist kein gültiges JSON: {exc}") from exc
    if not isinstance(config, dict):
        raise ProviderError("config.json enthält kein Objekt.")
    return config


def load_providers(config_path):
    """Die Rohliste aus der Datei — **ohne** die Env-Injektion von ``load_config()``."""
    config = _raw_config(config_path)
    providers = config.get("ai_providers")
    return [item for item in providers if isinstance(item, dict)] if isinstance(providers, list) else []


def load_default_id(config_path):
    """Der eingestellte Standard-Agent, roh gelesen."""
    config = _raw_config(config_path)
    value = config.get("ai_default_provider")
    return str(value) if isinstance(value, str) else ""


def env_managed_fields(provider, environ):
    """Welche Felder dieses Anbieters von aussen ueberschrieben werden.

    ``main._inject_env_vars`` gewinnt immer gegen die Datei. Ein Feld hier still
    speichern zu lassen, das beim naechsten Start wieder ueberschrieben wird,
    waere die schlechtere Ueberraschung als der Hinweis in der Oberflaeche.
    """
    managed = []
    index = provider.get("_index")
    if isinstance(index, int):
        for field in ("api_url", "model", "max_tokens", "temperature", "label"):
            if f"AI_PROVIDER_{index}_{field.upper()}" in environ:
                managed.append(field)
    pid = str(provider.get("id") or "")
    if pid and f"AI_KEY_{pid.upper()}" in environ:
        managed.append("api_key")
    return managed


def public_view(config_path, environ):
    """Was die Verwaltung zeigen darf: alles ausser dem Schluessel selbst."""
    providers = load_providers(config_path)
    items = []
    for index, provider in enumerate(providers):
        item = {field: provider.get(field) for field in FIELDS}
        item["id"] = str(provider.get("id") or "")
        item["has_key"] = bool(str(provider.get("api_key") or "").strip())
        item["uses_placeholder"] = _PLACEHOLDER in str(provider.get("api_url") or "")
        item["env_managed"] = env_managed_fields({**provider, "_index": index}, environ)
        items.append(item)
    return {"providers": items, "default_id": load_default_id(config_path)}


# ─── Validierung ──────────────────────────────────────────────────────
def _text(value, label, *, maximum, required=True):
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ProviderError(f"{label} muss Text sein.")
    value = value.strip()
    if required and not value:
        raise ProviderError(f"{label} darf nicht leer sein.")
    if len(value) > maximum:
        raise ProviderError(f"{label} ist länger als {maximum} Zeichen.")
    return value


def _number(value, label, *, minimum, maximum, integer):
    if value in (None, ""):
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, ValueError) as exc:
            raise ProviderError(f"{label} muss eine Zahl sein.") from exc
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProviderError(f"{label} muss eine Zahl sein.")
    if integer:
        if isinstance(value, float) and not value.is_integer():
            raise ProviderError(f"{label} muss eine ganze Zahl sein.")
        value = int(value)
    if value < minimum or value > maximum:
        raise ProviderError(f"{label} liegt außerhalb von {minimum} bis {maximum}.")
    return value


def _validate_url(raw, where):
    url = _text(raw, f"{where}: Die Adresse", maximum=_MAX_URL)
    probe = url.replace(_PLACEHOLDER, "127.0.0.1")
    if not probe.startswith(("http://", "https://")):
        raise ProviderError(
            f"{where}: Die Adresse muss mit http:// oder https:// beginnen "
            f"(der Platzhalter {_PLACEHOLDER} ist erlaubt)."
        )
    if any(char in url for char in ("\n", "\r", " ")):
        raise ProviderError(f"{where}: Die Adresse darf keine Leerzeichen oder Umbrüche enthalten.")
    return url


def _merge(candidate, previous):
    """Den kanonischen Eintrag bauen und unbekannte Altfelder mitnehmen."""
    merged = dict(previous or {})
    merged.update(candidate)
    for field in ("max_tokens", "temperature"):
        if candidate.get(field) is None:
            merged.pop(field, None)
    return merged


def _validate_provider(raw, seen_ids, previous_by_id):
    if not isinstance(raw, dict):
        raise ProviderError("Jeder Anbieter muss ein Objekt sein.")
    provider_id = _text(raw.get("id"), "Die Anbieter-ID", maximum=64)
    if not _ID_PATTERN.match(provider_id):
        raise ProviderError(
            f"Die ID „{provider_id}“ ist ungültig — erlaubt sind Kleinbuchstaben, Ziffern, _ und -."
        )
    if provider_id in seen_ids:
        raise ProviderError(f"Die ID „{provider_id}“ kommt doppelt vor.")
    seen_ids.add(provider_id)
    where = f"Anbieter „{provider_id}“"

    previous = previous_by_id.get(provider_id)
    candidate = {
        "id": provider_id,
        "label": _text(raw.get("label"), f"{where}: Die Beschriftung", maximum=_MAX_LABEL),
        "api_url": _validate_url(raw.get("api_url"), where),
        "model": _text(raw.get("model"), f"{where}: Das Modell", maximum=_MAX_MODEL),
        "max_tokens": _number(raw.get("max_tokens"), f"{where}: Die Token-Grenze",
                              minimum=1, maximum=_MAX_TOKENS_LIMIT, integer=True),
        "temperature": _number(raw.get("temperature"), f"{where}: Die Temperatur",
                               minimum=0, maximum=2, integer=False),
    }

    # Der Schluessel: fehlendes Feld heisst „unveraendert", damit die
    # Oberflaeche ihn nie zu Gesicht bekommen muss, um ihn zu behalten.
    if "api_key" in raw:
        key = raw.get("api_key")
        if key is None:
            candidate["api_key"] = str((previous or {}).get("api_key") or "")
        else:
            candidate["api_key"] = _text(key, f"{where}: Der Schlüssel",
                                         maximum=_MAX_KEY, required=False)
    elif previous is not None:
        candidate["api_key"] = str(previous.get("api_key") or "")

    return _merge(candidate, previous)


def validate(payload, default_id, previous_providers):
    """Prüft die komplette Liste und gibt sie in kanonischer Form zurück."""
    if not isinstance(payload, list):
        raise ProviderError("providers muss eine Liste sein.")
    if len(payload) > _MAX_PROVIDERS:
        raise ProviderError(f"Höchstens {_MAX_PROVIDERS} Anbieter.")

    previous_by_id = {
        str(item.get("id") or ""): item for item in previous_providers if isinstance(item, dict)
    }
    seen_ids = set()
    providers = [_validate_provider(raw, seen_ids, previous_by_id) for raw in payload]

    wanted = _text(default_id, "Der Standard-Agent", maximum=64, required=False)
    if wanted and wanted != CHAT_PROVIDER_ID and wanted not in seen_ids:
        raise ProviderError(
            f"Der Standard-Agent „{wanted}“ steht nicht in der Liste."
        )
    return providers, wanted


# ─── Schreiben ────────────────────────────────────────────────────────
def save(config_path, providers, default_id):
    """Ersetzt ausschliesslich ``ai_providers`` und ``ai_default_provider``."""
    def updater(current):
        if not (current or "").strip():
            raise ProviderError("config.json fehlt oder ist leer.")
        try:
            config = json.loads(current)
        except json.JSONDecodeError as exc:
            raise ProviderError(f"config.json ist kein gültiges JSON: {exc}") from exc
        config["ai_providers"] = providers
        if default_id:
            config["ai_default_provider"] = default_id
        else:
            config.pop("ai_default_provider", None)
        return json.dumps(config, ensure_ascii=False, indent=2) + "\n"

    update_text_file(config_path, updater)
    return providers


__all__ = [
    "CHAT_PROVIDER_ID", "FIELDS", "ProviderError", "env_managed_fields",
    "load_default_id", "load_providers", "public_view", "save", "validate",
]
