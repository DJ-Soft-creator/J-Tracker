"""HTTP-Oberfläche für Sprache (`/api/voice/...`).

Nur Request-Validierung und Serialisierung; die Fachlogik liegt in ``voice.py``.
Schreiben der Einstellungen bleibt Administratoren vorbehalten (global, wie
Templates und Anbieter). Transkription, Vorlesen, Nachbearbeitung und Benennen
stehen jedem angemeldeten Nutzer offen.
"""

import logging
import os
import sys
import tempfile

from flask import Blueprint, jsonify, request

import voice


voice_bp = Blueprint("voice", __name__, url_prefix="/api/voice")
logger = logging.getLogger(__name__)

MAX_AUDIO_BYTES = 32 * 1024 * 1024
POLISH_MAX_TOKENS = 4000
# Lokale Denk-Modelle (ArmyKnife-vt) dachten bei ~2 Minuten Diktat ueber
# 4000 Tokens nach und lieferten dann keinen Inhalt (502). Das Budget kappt
# das Nachdenken (llama.cpp ``thinking_budget_tokens``); das Limit waechst mit
# dem Text, weil die Antwort etwa so lang ist wie das Diktat.
VOICE_THINKING_BUDGET = 1024


def _polish_max_tokens(text):
    return max(POLISH_MAX_TOKENS, VOICE_THINKING_BUDGET + len(text) // 2)


def _main():
    return sys.modules.get("main") or sys.modules.get("__main__")


def _guard(*, write=False):
    main = _main()
    user = main._current_user() if main else None
    if not user:
        return None, (jsonify({"error": "Unauthorized"}), 401)
    if write:
        error = main.csrf_protect(lambda: None)()
        if error:
            return None, error
    return user, None


def _body():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _config():
    return _main().load_config()


def _settings_response(config, settings):
    provider = voice.resolve_provider(config, settings.get("provider_id"))
    providers = [
        {"id": item.get("id"), "label": item.get("label") or item.get("id")}
        for item in voice.local_providers(config)
    ]
    models = []
    models_error = ""
    if provider is not None:
        try:
            models = voice.discover_models(provider)
        except voice.VoiceError as exc:
            models_error = str(exc)
    user = _main()._current_user()
    return jsonify({
        "settings": settings,
        "providers": providers,
        "resolved_provider_id": (provider or {}).get("id", ""),
        "models": models,
        "models_error": models_error,
        "can_edit": bool(user) and user.get("admin") is True,
    })


# ─── Einstellungen ────────────────────────────────────────────────────
@voice_bp.route("/settings", methods=["GET"])
def get_settings():
    user, error = _guard()
    if error:
        return error
    main = _main()
    try:
        settings = voice.load_settings(main.CONFIG_PATH)
    except voice.VoiceError as exc:
        return jsonify({"error": str(exc)}), 500
    return _settings_response(_config(), settings)


@voice_bp.route("/settings", methods=["PUT"])
def put_settings():
    user, error = _guard(write=True)
    if error:
        return error
    if user.get("admin") is not True:
        return jsonify({"error": "Nur Administratoren dürfen Spracheinstellungen ändern."}), 403
    main = _main()
    payload = _body()
    try:
        current = voice.load_settings(main.CONFIG_PATH)
        candidate = {**current, **{key: payload[key] for key in payload if key in current or key == "presets"}}
        settings = voice.save_settings(main.CONFIG_PATH, candidate)
    except voice.VoiceError as exc:
        return jsonify({"error": str(exc)}), 400
    except OSError:
        return jsonify({"error": "Einstellungen konnten nicht gespeichert werden."}), 500
    return _settings_response(_config(), settings)


@voice_bp.route("/models", methods=["GET"])
def list_models():
    user, error = _guard()
    if error:
        return error
    config = _config()
    provider_id = request.args.get("provider_id") or ""
    provider = voice.resolve_provider(config, provider_id)
    if provider is None:
        return jsonify({"models": [], "error": "Kein lokaler Anbieter konfiguriert."}), 200
    try:
        models = voice.discover_models(provider)
    except voice.VoiceError as exc:
        return jsonify({"models": [], "error": str(exc)}), 200
    return jsonify({"models": models, "provider_id": provider.get("id")})


# ─── Transkription / Vorlesen / Nachbearbeitung ───────────────────────
def _resolve_for(config, provider_id):
    provider = voice.resolve_provider(config, provider_id)
    if provider is None:
        raise voice.VoiceError("Kein lokaler Voice-Anbieter konfiguriert.")
    return provider


@voice_bp.route("/transcribe", methods=["POST"])
def transcribe():
    user, error = _guard(write=True)
    if error:
        return error
    upload = request.files.get("file")
    if upload is None:
        return jsonify({"error": "Keine Audiodatei empfangen."}), 400
    raw = upload.read(MAX_AUDIO_BYTES + 1)
    if not raw:
        return jsonify({"error": "Die Audiodatei ist leer."}), 400
    if len(raw) > MAX_AUDIO_BYTES:
        return jsonify({"error": "Die Audiodatei ist zu groß."}), 413

    config = _config()
    try:
        provider = _resolve_for(config, request.form.get("provider_id") or "")
        base = voice.provider_base_url(provider.get("api_url"))
        model = (request.form.get("model") or "").strip() or voice.DEFAULT_ASR_MODEL
        handle = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
        try:
            handle.write(raw)
            handle.close()
            text = voice.transcribe(handle.name, base, model,
                                    language=request.form.get("language") or "de")
        finally:
            try:
                os.unlink(handle.name)
            except OSError:
                pass
    except voice.VoiceError as exc:
        return jsonify({"error": str(exc)}), 502
    return jsonify({"text": text})


@voice_bp.route("/polish", methods=["POST"])
def polish():
    user, error = _guard(write=True)
    if error:
        return error
    payload = _body()
    text = str(payload.get("text") or "").strip()
    if not text:
        return jsonify({"error": "Es gibt keinen Text zum Nachbearbeiten."}), 400

    main = _main()
    try:
        settings = voice.load_settings(main.CONFIG_PATH)
    except voice.VoiceError as exc:
        return jsonify({"error": str(exc)}), 500
    preset_id = str(payload.get("preset_id") or "").strip()
    preset = next((item for item in settings["presets"] if item["id"] == preset_id), None)
    if preset is None:
        return jsonify({"error": "Unbekanntes Preset."}), 404

    try:
        provider = _resolve_for(_config(), payload.get("provider_id") or settings.get("provider_id"))
    except voice.VoiceError as exc:
        return jsonify({"error": str(exc)}), 502
    model = settings.get("polish_model") or provider.get("model") or ""
    if model:
        provider = {**provider, "model": model}
    ai_function = {
        "system_prompt": preset["prompt"],
        "max_tokens": _polish_max_tokens(text),
        "thinking_budget": VOICE_THINKING_BUDGET,
        "temperature": 0.2,
        "require_content": True,
    }
    try:
        result = main._call_ai_api(provider, ai_function, text)
    except Exception as exc:  # noqa: BLE001 - Anbieterfehler direkt an die UI
        logger.warning("Nachbearbeitung fehlgeschlagen (%d Zeichen, Modell %s): %s",
                       len(text), provider.get("model"), exc)
        return jsonify({"error": f"Nachbearbeitung fehlgeschlagen: {exc}"}), 502
    return jsonify({"text": (result or "").strip()})


# Lokale Denk-Modelle (z. B. ArmyKnife-vt) verbrauchen vor der Antwort rund
# 500-600 Tokens fuers Nachdenken; mit 200 kam nie Inhalt an (502).
NAME_MAX_TOKENS = 2000


def _tag_resolver(user_id):
    """Vorhandene Hashtags fuers Modell und ein Filter fuer seine Antwort.

    Workflow-, Wissens- und Ziel-Hashtags loesen beim Senden Aktionen aus;
    gesperrte will der Nutzer nicht mehr sehen. Keiner davon darf ueber einen
    automatischen Titel in den Eintrag geraten.
    """
    import tagging

    catalogs = [tagging.read_catalog(user_id), tagging.read_catalog(family=True)]
    reserved = set()
    for catalog in catalogs:
        for key in ("ai_workflows", "knowledge_sources", "write_targets"):
            reserved |= set(catalog.get(key) or {})
        reserved |= set(catalog.get("blocked") or [])
    known = set()
    for catalog in catalogs:
        known |= set(catalog.get("canonical") or [])
    known -= reserved

    def resolve(raw):
        tag = tagging.normalise_tag(raw)
        if not tag or tag in reserved:
            return ""
        for catalog in catalogs:
            alias = (catalog.get("aliases") or {}).get(tag)
            if alias and alias not in reserved:
                return alias
        return tag

    return known, resolve


@voice_bp.route("/name", methods=["POST"])
def name():
    """Kurzen Namen und Hashtags fuer eine transkribierte Sprachnachricht."""
    user, error = _guard(write=True)
    if error:
        return error
    payload = _body()
    text = str(payload.get("text") or "").strip()
    if not text:
        return jsonify({"error": "Es gibt keinen Text zum Benennen."}), 400

    main = _main()
    try:
        settings = voice.load_settings(main.CONFIG_PATH)
        provider = _resolve_for(_config(), payload.get("provider_id") or settings.get("provider_id"))
    except voice.VoiceError as exc:
        return jsonify({"error": str(exc)}), 502
    model = settings.get("polish_model") or provider.get("model") or ""
    if model:
        provider = {**provider, "model": model}
    known, resolve = _tag_resolver(user["id"])
    ai_function = {
        "system_prompt": voice.naming_prompt(known, settings.get("name_prompt")),
        "max_tokens": NAME_MAX_TOKENS,
        "thinking_budget": VOICE_THINKING_BUDGET,
        "temperature": 0.2,
        "require_content": True,
    }
    try:
        answer = main._call_ai_api(provider, ai_function, text[:6000])
    except Exception as exc:  # noqa: BLE001 - Anbieterfehler direkt an die UI
        logger.warning("Benennen fehlgeschlagen (Modell %s): %s", provider.get("model"), exc)
        return jsonify({"error": f"Benennen fehlgeschlagen: {exc}"}), 502
    title, tags = voice.parse_name(answer, resolve)
    if not title and not tags:
        return jsonify({"error": "Das Modell hat keinen Namen geliefert."}), 502
    return jsonify({"title": title, "hashtags": tags})


@voice_bp.route("/speak", methods=["POST"])
def speak():
    user, error = _guard(write=True)
    if error:
        return error
    payload = _body()
    main = _main()
    try:
        settings = voice.load_settings(main.CONFIG_PATH)
    except voice.VoiceError as exc:
        return jsonify({"error": str(exc)}), 500
    try:
        provider = _resolve_for(_config(), payload.get("provider_id")
                                or settings.get("tts_provider_id") or settings.get("provider_id"))
    except voice.VoiceError as exc:
        return jsonify({"error": str(exc)}), 502
    model = str(payload.get("model") or "").strip() or settings.get("tts_model")
    tts_voice = str(payload.get("voice") or "").strip() or settings.get("tts_voice")
    try:
        audio, content_type = voice.synthesize(
            payload.get("text"), voice.provider_base_url(provider.get("api_url")), model, tts_voice
        )
    except voice.VoiceError as exc:
        return jsonify({"error": str(exc)}), 502
    response = _main().make_response(audio)
    response.headers["Content-Type"] = content_type
    response.headers["Cache-Control"] = "no-store"
    return response


__all__ = ["voice_bp"]
