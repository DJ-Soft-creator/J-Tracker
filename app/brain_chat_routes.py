"""HTTP-Oberflaeche fuer „Chat with your Notes" (`/api/brain/chat/...`).

AI maintenance contract: nur Request-Validierung und Serialisierung. Speicher,
Modellaufruf und Werkzeugschleife liegen in brain_chat.py.

Die Antworten tragen nie den gespeicherten API-Schluessel — der Browser sieht
ausschliesslich ``brain_chat.public_settings``.
"""

import sys

from flask import Blueprint, jsonify, request

import background_runs
import brain_chat
import brain_chat_sessions
from brain_chat import BrainChatError

brain_chat_bp = Blueprint("brain_chat", __name__, url_prefix="/api/brain/chat")


def _main():
    return sys.modules.get("main") or sys.modules.get("__main__")


def _guard(*, write=False):
    """Resolve the caller, or return the response that must be sent instead."""
    main = _main()
    user = main._current_user() if main else None
    if not user:
        return None, (jsonify({"error": "Unauthorized"}), 401)
    if write:
        error = main.csrf_protect(lambda: None)()
        if error:
            return None, error
    return user, None


def _no_store(payload, status=200):
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store"
    return response, status


@brain_chat_bp.route("/settings", methods=["GET"])
def settings_get():
    user, error = _guard()
    if error:
        return error
    settings = brain_chat.load_settings(user["id"])
    return _no_store({
        **brain_chat.public_settings(settings),
        "tools": [tool["function"]["name"] for tool in brain_chat.tool_specs()],
        "max_rounds": brain_chat.MAX_ROUNDS,
    })


@brain_chat_bp.route("/settings", methods=["POST"])
def settings_set():
    user, error = _guard(write=True)
    if error:
        return error
    data = request.get_json(silent=True) or {}
    try:
        saved = brain_chat.save_settings(user["id"], data)
    except BrainChatError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True, **brain_chat.public_settings(saved)})


@brain_chat_bp.route("/models", methods=["GET"])
def models():
    """Live model list of one endpoint — never a cached guess."""
    user, error = _guard()
    if error:
        return error
    base_url = str(request.args.get("base_url") or "")
    if not base_url:
        base_url = brain_chat.load_settings(user["id"])["base_url"]
    try:
        found = brain_chat.discover_models(base_url)
    except BrainChatError as exc:
        return jsonify({"error": str(exc)}), 502
    return _no_store({"models": found, "base_url": base_url})


@brain_chat_bp.route("/ask", methods=["POST"])
def ask():
    """Die Frage als Serverauftrag einreihen — die Antwort holt sich der Browser.

    Frueher lief die ganze Werkzeugschleife in diesem einen Request. Eine Antwort
    kann Minuten dauern; ein gesperrtes Handy warf sie weg, obwohl der Server
    fertig gerechnet hatte. Jetzt ueberlebt sie den geschlossenen Browser und
    steht anschliessend im Chatverlauf des Tages.
    """
    user, error = _guard(write=True)
    if error:
        return error
    data = request.get_json(silent=True) or {}
    session_path = str(data.get("session_path") or "")
    if session_path and not brain_chat_sessions.is_session_path(session_path):
        return jsonify({"error": "Ungueltiger Chatverlauf."}), 400
    try:
        history = brain_chat.normalise_history(data.get("messages"))
    except BrainChatError as exc:
        return jsonify({"error": str(exc)}), 400
    try:
        record = background_runs.start(
            user["id"], brain_chat.RUN_KIND,
            {"messages": history, "session_path": session_path},
            label="Frage an die Notizen",
        )
    except background_runs.RunConflict as exc:
        running = background_runs.active(user["id"], brain_chat.RUN_KIND)
        return jsonify({
            "error": str(exc),
            "run": background_runs.public_run(running) if running else None,
        }), 409
    except background_runs.RunError as exc:
        return jsonify({"error": str(exc)}), 400
    return _no_store({"run": background_runs.public_run(record)}, 202)


@brain_chat_bp.route("/session", methods=["GET"])
def session_get():
    """Einen gespeicherten Verlauf zurueckgeben, damit der Browser ihn fortsetzt."""
    user, error = _guard()
    if error:
        return error
    session = brain_chat_sessions.read_session(user["id"], request.args.get("path", ""))
    if session is None:
        return jsonify({"error": "Chatverlauf nicht gefunden"}), 404
    return _no_store(session)


@brain_chat_bp.route("/session", methods=["DELETE"])
def session_delete():
    """Einen Verlauf endgueltig verwerfen — nur auf ausdruecklichen Wunsch."""
    user, error = _guard(write=True)
    if error:
        return error
    data = request.get_json(silent=True) or {}
    if not brain_chat_sessions.discard(user["id"], str(data.get("path") or "")):
        return jsonify({"error": "Chatverlauf nicht gefunden"}), 404
    return jsonify({"ok": True})


__all__ = ["brain_chat_bp"]
