"""HTTP-Oberflaeche fuer den Schreib-Agenten (`/api/write-agent/...`).

AI maintenance contract: nur Request-Validierung und Serialisierung. Werkzeuge,
Schreibziele und Werkzeugschleife liegen in write_agent.py.
"""

import sys

from flask import Blueprint, jsonify, request

import agent_knowledge
import background_runs
import write_agent
from write_agent import WriteAgentError

write_agent_bp = Blueprint("write_agent", __name__, url_prefix="/api/write-agent")


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


@write_agent_bp.route("/state", methods=["GET"])
def state():
    """Was die Oberflaeche beim Oeffnen braucht: Agenten, Schreibziele, Wissen."""
    user, error = _guard()
    if error:
        return error
    providers = write_agent.provider_options(user["id"])
    knowledge = agent_knowledge.listing(user["id"])
    running = background_runs.active(user["id"], write_agent.RUN_KIND)
    return _no_store({
        "configured": bool(providers),
        "providers": providers,
        "default_provider": write_agent.default_provider_id(providers),
        "goals": write_agent.goal_options(),
        "knowledge": knowledge.get("hashtags", []),
        "max_rounds": write_agent.MAX_ROUNDS,
        "max_chars": write_agent.MAX_TEXT_CHARS,
        "run": background_runs.public_run(running) if running else None,
    })


@write_agent_bp.route("/ask", methods=["POST"])
def ask():
    """Den Auftrag einreihen — das Ergebnis holt der Browser ueber /api/runs/<id>.

    Der Text reist mit dem Auftrag und wird nirgends abgelegt: der Agent
    schlaegt vor, uebernommen wird im Fenster.
    """
    user, error = _guard(write=True)
    if error:
        return error
    data = request.get_json(silent=True) or {}
    try:
        history = write_agent.normalise_history(data.get("messages"))
        text = write_agent.clean_text(data.get("text"))
    except (WriteAgentError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400

    # Der Browser nennt eine Anbieter- und eine Schreibziel-ID, nie eine
    # Adresse, nie einen Schluessel und nie einen Systemtext. Ein unbekannter
    # Wunsch faellt serverseitig auf die Standardverbindung bzw. auf kein Ziel
    # zurueck.
    params = {
        "messages": history,
        "text": text,
        "provider_id": str(data.get("provider_id") or ""),
        "goal_id": str(data.get("goal_id") or ""),
        "knowledge": write_agent.clean_tags(data.get("knowledge")),
    }
    try:
        record = background_runs.start(
            user["id"], write_agent.RUN_KIND, params, label="Text überarbeiten")
    except background_runs.RunConflict as exc:
        running = background_runs.active(user["id"], write_agent.RUN_KIND)
        return jsonify({
            "error": str(exc),
            "run": background_runs.public_run(running) if running else None,
        }), 409
    except background_runs.RunError as exc:
        return jsonify({"error": str(exc)}), 400
    return _no_store({"run": background_runs.public_run(record)}, 202)


__all__ = ["write_agent_bp"]
