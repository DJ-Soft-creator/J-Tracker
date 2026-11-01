"""Sprache im Journal: Transkription (ASR), Vorlesen (TTS) und Presets.

AI maintenance contract:
Own the ``voice`` key in config.json — validation, canonical shape, secret-free
persistence — plus the two reusable HTTP calls against a local OpenAI-compatible
voice endpoint. HTTP routes stay in ``voice_routes.py``.

Warum ein eigenes Modul: Dasselbe Muster wie ``journal_templates`` und
``ai_providers``. Die Anbieter kommen aus ``ai_providers`` (nur lokale
Endpunkte, D-015); hier liegen nur die gewaehlten Modelle und die
Nachbearbeitungs-Presets. Geschrieben wird immer die Rohdatei unter der
gemeinsamen Sperre, damit ``load_config()`` mit seinen env-injizierten
Geheimnissen nie zurueckgeschrieben wird.

TTS ist bewusst als freie Funktion gebaut (``synthesize``), damit weitere
Features sie ohne Route wiederverwenden koennen.
"""

import json
import re
import urllib.error
import urllib.request
from pathlib import Path

from scheduling import read_text_file, update_text_file


DEFAULT_ASR_MODEL = "asr-a"
DEFAULT_TTS_MODEL = "tts-t"
DEFAULT_TTS_VOICE = "alloy"

_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_MAX_MODEL = 160
_MAX_VOICE = 64
_MAX_LABEL = 80
_MAX_PROMPT = 4000
_MAX_PRESETS = 40
_TEXT_LIMIT = 8000

DEFAULT_PRESETS = [
    {
        "id": "feinschliff",
        "label": "Feinschliff",
        "prompt": (
            "Überarbeite den diktierten Text behutsam: korrigiere Rechtschreibung, "
            "Grammatik und Zeichensetzung, entferne Füllwörter und Versprecher. "
            "Behalte Wortwahl, Ton und Inhalt bei. Gib ausschließlich den "
            "überarbeiteten Text zurück, ohne Vorrede."
        ),
    },
    {
        "id": "satzverbesserungen",
        "label": "Satzverbesserungen",
        "prompt": (
            "Überarbeite den diktierten Text: bilde klare, gut lesbare Sätze, "
            "straffe Wiederholungen und ordne den Gedankengang. Inhalt und "
            "Aussage bleiben unverändert. Gib ausschließlich den überarbeiteten "
            "Text zurück, ohne Vorrede."
        ),
    },
]


class VoiceError(ValueError):
    """Eine Spracheinstellung oder ein Aufruf verletzt den Vertrag."""


# ─── Konfiguration ────────────────────────────────────────────────────
def _raw_config(config_path):
    raw = read_text_file(config_path)
    if not (raw or "").strip():
        raise VoiceError("config.json fehlt oder ist leer.")
    try:
        config = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise VoiceError(f"config.json ist kein gültiges JSON: {exc}") from exc
    if not isinstance(config, dict):
        raise VoiceError("config.json enthält kein Objekt.")
    return config


def _clean_preset(raw, seen):
    if not isinstance(raw, dict):
        raise VoiceError("Jedes Preset muss ein Objekt sein.")
    preset_id = str(raw.get("id") or "").strip()
    if not _ID_PATTERN.match(preset_id):
        raise VoiceError(
            "Die Preset-ID ist ungültig — erlaubt sind Kleinbuchstaben, Ziffern, _ und -."
        )
    if preset_id in seen:
        raise VoiceError(f"Die Preset-ID „{preset_id}“ kommt doppelt vor.")
    seen.add(preset_id)
    label = str(raw.get("label") or "").strip() or preset_id
    if len(label) > _MAX_LABEL:
        raise VoiceError("Der Preset-Name ist zu lang.")
    prompt = str(raw.get("prompt") or "").strip()
    if not prompt:
        raise VoiceError(f"Preset „{label}“ braucht eine Anweisung.")
    if len(prompt) > _MAX_PROMPT:
        raise VoiceError(f"Preset „{label}“ ist zu lang.")
    return {"id": preset_id, "label": label, "prompt": prompt}


def _clean_settings(raw):
    raw = raw if isinstance(raw, dict) else {}
    presets = raw.get("presets")
    if not isinstance(presets, list):
        presets = DEFAULT_PRESETS
    if len(presets) > _MAX_PRESETS:
        raise VoiceError(f"Höchstens {_MAX_PRESETS} Presets.")
    seen = set()
    cleaned_presets = [_clean_preset(item, seen) for item in presets]
    return {
        "provider_id": str(raw.get("provider_id") or "").strip(),
        "asr_model": str(raw.get("asr_model") or "").strip() or DEFAULT_ASR_MODEL,
        "polish_model": str(raw.get("polish_model") or "").strip(),
        # Vorlesen hat einen eigenen Anbieter (Burger → Schreiben); leer heisst
        # „wie die Aufnahme“, damit bestehende Konfigurationen weiterlaufen.
        "tts_provider_id": str(raw.get("tts_provider_id") or "").strip(),
        "tts_model": str(raw.get("tts_model") or "").strip() or DEFAULT_TTS_MODEL,
        "tts_voice": str(raw.get("tts_voice") or "").strip() or DEFAULT_TTS_VOICE,
        "presets": cleaned_presets,
        # Benennen nach der Aufnahme: Titel plus Hashtags an der Sprachnachricht.
        "name_enabled": raw.get("name_enabled") is not False,
        "name_prompt": str(raw.get("name_prompt") or "").strip() or DEFAULT_NAME_TASK,
    }


def load_settings(config_path):
    """Die rohe ``voice``-Sektion, ohne die Env-Injektion aus ``load_config``."""
    config = _raw_config(config_path)
    return _clean_settings(config.get("voice"))


def save_settings(config_path, candidate):
    """Ersetzt ausschliesslich ``voice``; alles andere bleibt unangetastet."""
    cleaned = _clean_settings(candidate)
    for field_name in ("asr_model", "polish_model", "tts_model"):
        if len(cleaned[field_name]) > _MAX_MODEL:
            raise VoiceError("Der Modellname ist zu lang.")
    if len(cleaned["tts_voice"]) > _MAX_VOICE:
        raise VoiceError("Der Voice-Name ist zu lang.")
    if len(cleaned["name_prompt"]) > _MAX_PROMPT:
        raise VoiceError("Die Benennen-Anweisung ist zu lang.")

    def updater(current):
        if not (current or "").strip():
            raise VoiceError("config.json fehlt oder ist leer.")
        try:
            config = json.loads(current)
        except json.JSONDecodeError as exc:
            raise VoiceError(f"config.json ist kein gültiges JSON: {exc}") from exc
        config["voice"] = cleaned
        return json.dumps(config, ensure_ascii=False, indent=2) + "\n"

    update_text_file(config_path, updater)
    return cleaned


# ─── Anbieter und Endpunkt ────────────────────────────────────────────
def provider_base_url(api_url):
    """Aus der Chat-URL die Basis fuer ``/audio/...`` und ``/models``.

    ``ai_providers`` tragen die volle Adresse ``…/v1/chat/completions``; die
    Sprach-API haengt auf derselben Basis ``…/v1``.
    """
    text = str(api_url or "").strip().rstrip("/")
    if text.endswith("/chat/completions"):
        text = text[: -len("/chat/completions")]
    return text


def _is_local(url):
    import journal_analysis_providers
    return journal_analysis_providers.is_local_endpoint(url)


def local_providers(config):
    """Alle Anbieter mit lokalem Endpunkt — nur die sind fuer Sprache erlaubt."""
    result = []
    for provider in config.get("ai_providers") or []:
        if not isinstance(provider, dict):
            continue
        url = provider.get("api_url") or ""
        if not _is_local(url):
            continue
        result.append(provider)
    return result


def resolve_provider(config, provider_id=""):
    """Den gewaehlten (oder ersten lokalen) Anbieter aufloesen."""
    if provider_id:
        for provider in local_providers(config):
            if provider.get("id") == provider_id:
                return provider
        return None
    providers = local_providers(config)
    return providers[0] if providers else None


# ─── Modell-Liste ─────────────────────────────────────────────────────
def discover_models(provider):
    """Live-Modell-Liste des Endpunkts — dieselbe wie beim Chat."""
    import agent_projects
    base = provider_base_url(provider.get("api_url"))
    try:
        return agent_projects.discover_models("openai", base)
    except agent_projects.AgentProjectError as exc:
        raise VoiceError(str(exc)) from exc


# ─── HTTP-Aufrufe ─────────────────────────────────────────────────────
def _timeout():
    import os
    try:
        return max(30, min(3600, int(os.environ.get("VOICE_AI_TIMEOUT", "600"))))
    except (TypeError, ValueError):
        return 600


def _opener():
    # Kein Proxy dazwischen: die Sprachdienste sind ausdruecklich lokal (D-015).
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _http_detail(exc):
    """Kurzer Auszug aus der Fehlerantwort, ohne den Aufrufer zu ueberfluten."""
    try:
        body = exc.read(600).decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 - nur Zusatzinfo
        return ""
    try:
        parsed = json.loads(body)
        error = parsed.get("error") if isinstance(parsed, dict) else None
        if isinstance(error, dict):
            error = error.get("message")
        if isinstance(error, str) and error.strip():
            body = error
    except ValueError:
        pass
    body = " ".join(body.split())[:200]
    return f" ({body})" if body else ""


def transcribe(file_path, base_url, model, *, language="de", time_limit=None):
    """Eine Audiodatei lokal transkribieren; gibt den reinen Text zurueck.

    ``file_path`` darf ``str`` oder ``Path`` sein - die Route uebergab den Namen
    einer Temp-Datei als String, und ``str.read_bytes`` liess jede Aufnahme mit
    500 scheitern (Vorschau leer, Nachbearbeitung startete nie).
    """
    audio = Path(file_path).read_bytes()
    boundary = "----journlvoice"
    parts = [
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="model"\r\n\r\n'.encode(),
        str(model).encode(), b"\r\n",
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="language"\r\n\r\n'.encode(),
        str(language).encode(), b"\r\n",
        f"--{boundary}\r\n".encode(),
        'Content-Disposition: form-data; name="response_format"\r\n\r\n'.encode(),
        b"verbose_json\r\n",
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="file"; filename="audio.wav"\r\n'.encode(),
        b"Content-Type: application/octet-stream\r\n\r\n",
        audio, b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ]
    request = urllib.request.Request(
        base_url.rstrip("/") + "/audio/transcriptions", data=b"".join(parts), method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with _opener().open(request, timeout=time_limit or _timeout()) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # Modell und Antwort des Endpunkts mitnennen: ein falsch gewaehltes
        # Modell (z. B. ein TTS-Modell statt ``asr-a``) war sonst nicht von
        # einem ausgefallenen MacMini zu unterscheiden.
        raise VoiceError(
            f"Transkription mit „{model}“ fehlgeschlagen: der lokale "
            f"Voice-Endpunkt meldet HTTP {exc.code}{_http_detail(exc)}."
        ) from exc
    except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise VoiceError(
            f"Transkription mit „{model}“ fehlgeschlagen: der lokale "
            "Voice-Endpunkt ist nicht erreichbar oder hat einen Fehler gemeldet."
        ) from exc
    text = payload.get("text") if isinstance(payload, dict) else None
    if not isinstance(text, str) or not text.strip():
        raise VoiceError("Der Voice-Endpunkt hat kein verwertbares Transkript geliefert.")
    return text.strip()


def synthesize(text, base_url, model, voice, *, time_limit=None):
    """Text in Audio verwandeln; gibt ``(bytes, content_type)`` zurueck.

    Bewusst eine freie Funktion: weitere Features sollen sie ohne Route nutzen.
    """
    body = str(text or "").strip()
    if not body:
        raise VoiceError("Es gibt keinen Text zum Vorlesen.")
    if len(body) > _TEXT_LIMIT:
        raise VoiceError("Der Text ist zum Vorlesen zu lang.")
    # WAV ausdruecklich anfordern: ohne Angabe liefert z. B. ``tts-David`` auf
    # dem MacMini MP3 - und das kam als HTTP 200 mit leerem Koerper an.
    payload = json.dumps(
        {"model": model, "input": body, "voice": voice, "response_format": "wav"},
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        base_url.rstrip("/") + "/audio/speech", data=payload, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with _opener().open(request, timeout=time_limit or _timeout()) as response:
            audio = response.read()
            content_type = response.headers.get("Content-Type") or "audio/wav"
    except (OSError, ValueError, urllib.error.URLError) as exc:
        raise VoiceError(
            "Vorlesen fehlgeschlagen: der lokale Voice-Endpunkt ist nicht "
            "erreichbar oder hat einen Fehler gemeldet."
        ) from exc
    if not audio:
        raise VoiceError(f"Der Voice-Endpunkt hat mit „{model}“ kein Audio geliefert.")
    return audio, content_type


# ─── Sprachnachricht benennen ─────────────────────────────────────────
# Die Sternchen-Aufnahme laeuft ohnehin ueber ein LLM; sie bekommt dabei einen
# kurzen Namen und passende Hashtags. Beides landet im Titel der Aufnahme
# („Sprachnachricht: Name #tag | Aufnahmezeit …“) und ist so durchsuchbar.
NAME_MAX_TAGS = 3
_NAME_MAX_TITLE = 60
_NAME_MAX_KNOWN = 150
_NAME_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)
_NAME_TAG_RE = re.compile(r"(?<![\w#])#[\w-]+", re.UNICODE)

# Die Aufgabe (worauf Titel und Hashtags achten) ist im Panel einstellbar; das
# Antwortformat bleibt fest, sonst koennte ``parse_name`` nichts mehr lesen.
DEFAULT_NAME_TASK = (
    "Gib ihr einen Titel aus höchstens sechs Wörtern auf Deutsch, der sagt, "
    f"worum es geht, ohne Datum. Wähle ein bis {NAME_MAX_TAGS} passende Hashtags."
)
_NAME_FORMAT = (
    "Antworte ausschließlich mit JSON der Form "
    '{"title": "...", "hashtags": ["...", "..."]}. '
    "title ohne Anführungszeichen und ohne #. "
    f"hashtags: höchstens {NAME_MAX_TAGS}, kleingeschrieben, ohne #, "
    "Wörter mit Bindestrich verbunden."
)
NAME_PROMPT = (
    "Du benennst eine diktierte Sprachnachricht für ein persönliches Journal. "
    + DEFAULT_NAME_TASK + " " + _NAME_FORMAT
)


def naming_prompt(known_tags, task=None):
    """Systemprompt fuers Benennen; vorhandene Hashtags werden bevorzugt.

    ``task`` ersetzt die Standard-Aufgabe (aus den Einstellungen). Nur die frei
    verwendbaren Hashtags gehen an das Modell - Workflow-, Wissens- und
    Ziel-Hashtags loesen Aktionen aus und duerfen nie vorgeschlagen werden.
    """
    task = str(task or "").strip() or DEFAULT_NAME_TASK
    prompt = ("Du benennst eine diktierte Sprachnachricht für ein persönliches Journal. "
              + task + " " + _NAME_FORMAT)
    tags = sorted({str(tag) for tag in known_tags if tag})[:_NAME_MAX_KNOWN]
    if not tags:
        return prompt
    return prompt + (
        " Verwende bevorzugt passende Hashtags aus dieser Liste und erfinde "
        "nur dann einen neuen, wenn keiner passt: " + ", ".join(tags) + "."
    )


def parse_name(answer, resolve_tag):
    """Titel und Hashtags aus der Modellantwort ziehen.

    ``resolve_tag`` normalisiert einen Hashtag (Alias -> kanonisch) und gibt
    ``""`` fuer gesperrte oder ungueltige zurueck. Lokale Modelle antworten
    nicht immer sauber: JSON mit Vorrede oder Codezaun wird toleriert, sonst
    gilt die erste Zeile als Titel.
    """
    text = str(answer or "").strip()
    data = None
    match = _NAME_JSON_RE.search(text)
    if match:
        try:
            data = json.loads(match.group(0))
        except ValueError:
            data = None
    if isinstance(data, dict):
        title = str(data.get("title") or "")
        raw_tags = data.get("hashtags") or data.get("tags") or []
        if isinstance(raw_tags, str):
            raw_tags = raw_tags.replace(",", " ").split()
    else:
        first = next((line for line in text.splitlines() if line.strip()), "")
        title = first
        raw_tags = _NAME_TAG_RE.findall(first)
    title = _NAME_TAG_RE.sub("", title)
    title = " ".join(title.replace('"', " ").replace("„", " ").replace("“", " ").split()).strip(" .:-")
    if len(title) > _NAME_MAX_TITLE:
        title = title[:_NAME_MAX_TITLE].rsplit(" ", 1)[0].rstrip(" ,.:-")
    tags = []
    for raw in raw_tags if isinstance(raw_tags, list) else []:
        tag = resolve_tag(str(raw or ""))
        if tag and tag not in tags:
            tags.append(tag)
        if len(tags) >= NAME_MAX_TAGS:
            break
    return title, tags


__all__ = [
    "DEFAULT_ASR_MODEL", "DEFAULT_TTS_MODEL", "DEFAULT_TTS_VOICE", "DEFAULT_NAME_TASK", "DEFAULT_PRESETS",
    "NAME_MAX_TAGS", "NAME_PROMPT", "VoiceError", "discover_models", "load_settings",
    "local_providers", "naming_prompt", "parse_name", "provider_base_url", "resolve_provider",
    "save_settings", "synthesize", "transcribe",
]
