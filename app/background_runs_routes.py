"""HTTP-Oberflaeche fuer serverseitige Laeufe (`/api/runs/...`).

AI maintenance contract: nur Request-Validierung und Serialisierung. Zustand und
Arbeit liegen in background_runs.py, die Fachlogik im jeweiligen Domaenenmodul.

Gestartet wird ein Lauf immer fachlich (z. B. `/api/brain/tagging/start`);
angesehen, abgebrochen und weggeraeumt wird er hier — fuer alle Laufarten gleich,
damit die Oberflaeche nur eine Abfrage kennen muss.
"""

import sys

from flask import Blueprint, jsonify, request

import background_runs

background_runs_bp = Blueprint("background_runs", __name__)


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


def _no_store(payload, status=200):
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store"
    return response, status


@background_runs_bp.route("/api/runs", methods=["GET"])
def list_runs():
    """Laufende zuerst — damit die Oberflaeche sich nach einer Pause wieder anhängt."""
    user, error = _guard()
    if error:
        return error
    kind = request.args.get("kind") or None
    runs = [
        {**background_runs.public_run(record), "log": [], "log_from": 0}
        for record in background_runs.listing(user["id"], kind)
    ]
    return _no_store({"runs": runs})


@background_runs_bp.route("/api/runs/<run_id>", methods=["GET"])
def get_run(run_id):
    """Ein Lauf mit dem Protokollrest ab ``log_from``."""
    user, error = _guard()
    if error:
        return error
    record = background_runs.read(user["id"], run_id)
    if record is None:
        return jsonify({"error": "Auftrag nicht gefunden"}), 404
    return _no_store(background_runs.public_run(record, request.args.get("log_from", 0)))


@background_runs_bp.route("/api/runs/<run_id>/cancel", methods=["POST"])
def cancel_run(run_id):
    user, error = _guard(write=True)
    if error:
        return error
    record = background_runs.cancel(user["id"], run_id)
    if record is None:
        return jsonify({"error": "Auftrag nicht gefunden"}), 404
    return _no_store(background_runs.public_run(record))


@background_runs_bp.route("/api/runs/<run_id>", methods=["DELETE"])
def discard_run(run_id):
    user, error = _guard(write=True)
    if error:
        return error
    try:
        removed = background_runs.discard(user["id"], run_id)
    except background_runs.RunConflict as exc:
        return jsonify({"error": str(exc)}), 409
    if not removed:
        return jsonify({"error": "Auftrag nicht gefunden"}), 404
    return jsonify({"ok": True})


__all__ = ["background_runs_bp"]
