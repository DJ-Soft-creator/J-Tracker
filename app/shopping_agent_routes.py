"""HTTP-Oberflaeche fuer den Einkaufslisten-Agenten (`/api/family/shopping-agent/...`).

AI maintenance contract: nur Request-Validierung und Serialisierung.
Werkzeuge, Rueckwaertsgang und Werkzeugschleife liegen in shopping_agent.py.
"""

import sys

from flask import Blueprint, jsonify, request

import background_runs
import shopping_agent
from shopping_agent import ShoppingAgentError

shopping_agent_bp = Blueprint("shopping_agent", __name__, url_prefix="/api/family/shopping-agent")


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


@shopping_agent_bp.route("/<project_id>/state", methods=["GET"])
def state(project_id):
    """Was die Oberflaeche beim Oeffnen braucht: Verbindung, Laeden, Rueckwaertsgang."""
    user, error = _guard()
    if error:
        return error
    providers = shopping_agent.provider_options(user["id"])
    try:
        # list_items zuerst: es ist das einzige Werkzeug hier, das die Liste
        # wirklich anfasst, und damit die Stelle, an der Sichtbarkeit und
        # Listentyp geprueft werden. Der Wissenskatalog allein wuerde jede
        # fremde Liste mit 200 beantworten.
        listed = shopping_agent.run_tool(user["id"], project_id, "list_items", {})
        knowledge = shopping_agent.run_tool(user["id"], project_id, "list_knowledge", {})
    except ShoppingAgentError as exc:
        return jsonify({"error": str(exc)}), 404
    running = background_runs.active(user["id"], shopping_agent.RUN_KIND)
    return _no_store({
        "title": listed.get("liste", ""),
        "configured": bool(providers),
        "providers": providers,
        "default_provider": shopping_agent.default_provider_id(providers),
        "knowledge": knowledge.get("hashtags", []),
        "can_undo": bool(shopping_agent.undo_available(user["id"], project_id)),
        "max_rounds": shopping_agent.MAX_ROUNDS,
        "run": background_runs.public_run(running) if running else None,
    })


@shopping_agent_bp.route("/<project_id>/ask", methods=["POST"])
def ask(project_id):
    """Den Auftrag einreihen — das Ergebnis holt der Browser ueber /api/runs/<id>."""
    user, error = _guard(write=True)
    if error:
        return error
    data = request.get_json(silent=True) or {}
    try:
        history = shopping_agent.normalise_history(data.get("messages"))
    except ShoppingAgentError as exc:
        return jsonify({"error": str(exc)}), 400
    # Der Browser nennt eine Anbieter-ID, nie eine Adresse und nie einen
    # Schluessel. Ein unbekannter Wunsch faellt serverseitig auf die
    # Standardverbindung zurueck.
    provider_id = str(data.get("provider_id") or "")
    try:
        record = background_runs.start(
            user["id"], shopping_agent.RUN_KIND,
            {"messages": history, "project_id": project_id, "provider_id": provider_id},
            label="Einkaufsliste bearbeiten",
        )
    except background_runs.RunConflict as exc:
        running = background_runs.active(user["id"], shopping_agent.RUN_KIND)
        return jsonify({
            "error": str(exc),
            "run": background_runs.public_run(running) if running else None,
        }), 409
    except background_runs.RunError as exc:
        return jsonify({"error": str(exc)}), 400
    return _no_store({"run": background_runs.public_run(record)}, 202)


@shopping_agent_bp.route("/<project_id>/undo", methods=["POST"])
def undo(project_id):
    """Den Stand vor dem letzten Agentenlauf wiederherstellen."""
    user, error = _guard(write=True)
    if error:
        return error
    try:
        result = shopping_agent.undo(user["id"], project_id)
    except ShoppingAgentError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True, **result})


__all__ = ["shopping_agent_bp"]
