"""HTTP surface for Agent-Projekte (`/api/agent-projects/...`).

Every handler is a thin, validating shell around ``agent_projects`` and
``agent_project_jobs``.  Job polling deliberately reuses the existing
``/api/write-ai/jobs/<id>`` contract so there is exactly one status machine for
host agent jobs.
"""

import sys

from flask import Blueprint, jsonify, request

import agent_project_jobs
import agent_projects
from agent_projects import AgentProjectError


agent_projects_bp = Blueprint("agent_projects", __name__, url_prefix="/api/agent-projects")


def _main():
    return sys.modules.get("main") or sys.modules.get("__main__")


def _user():
    main = _main()
    return main._current_user() if main else None


def _csrf_error():
    main = _main()
    return main.csrf_protect(lambda: None)() if main else (jsonify({"error": "Unauthorized"}), 401)


def _enqueue_rebuild(user_id):
    main = _main()
    if main:
        main.brain_module.enqueue_rebuild(user_id)


def _guard(*, write=False):
    """Resolve the caller, or return the response that must be sent instead."""
    user = _user()
    if not user:
        return None, (jsonify({"error": "Unauthorized"}), 401)
    if write:
        error = _csrf_error()
        if error:
            return None, error
    return user, None


def _no_store(payload, status=200):
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store"
    return response, status


def _public_connection(connection):
    """Connections carry no secrets, only the name of a host environment var."""
    return {key: connection[key] for key in
            ("id", "label", "kind", "base_url", "runner", "provider_id", "api_key_env")}


def _project_view(user_id, project, roots, connections):
    root = next((item for item in roots if item["id"] == project["root_id"]), None)
    connection = next((item for item in connections if item["id"] == project["connection_id"]), None)
    problems = []
    if not root:
        problems.append("Der Host-Ordner dieses Projekts ist nicht mehr freigegeben.")
    elif project["mode"] in {"write", "execute"} and not root["writable"]:
        problems.append("Der Host-Ordner ist nur lesend freigegeben.")
    if not connection:
        problems.append("Die Verbindung dieses Projekts existiert nicht mehr.")
    return {
        **project,
        "root_label": root["label"] if root else "",
        "root_path": root["path"] if root else "",
        "root_writable": bool(root and root["writable"]),
        "connection_label": connection["label"] if connection else "",
        "connection_kind": connection["kind"] if connection else "",
        "runner": connection["runner"] if connection else "",
        "runner_session": agent_project_jobs.runner_session_for(user_id, project),
        "has_session": bool(project.get("session_id") and project.get("session_path")),
        "problems": problems,
    }


@agent_projects_bp.route("/bootstrap", methods=["GET"])
def bootstrap():
    user, error = _guard()
    if error:
        return error
    store = agent_projects.load_store(user["id"])
    roots = agent_projects.project_roots()
    connections = [_public_connection(item) for item in store["connections"]]
    main = _main()
    today = main.get_tz_aware_now()[0].strftime("%Y-%m-%d") if main else ""
    return _no_store({
        "roots": roots,
        "connections": connections,
        "projects": [_project_view(user["id"], item, roots, store["connections"])
                     for item in store["projects"]],
        "kinds": list(agent_projects.KINDS),
        "runners": list(agent_projects.RUNNERS),
        "modes": list(agent_projects.MODES),
        "today": today,
        "max_projects": agent_projects.MAX_PROJECTS,
        "max_connections": agent_projects.MAX_CONNECTIONS,
        "roots_configured": bool(roots),
    })


@agent_projects_bp.route("/connections", methods=["POST"])
def save_connection():
    user, error = _guard(write=True)
    if error:
        return error
    data = request.get_json(silent=True) or {}
    action = str(data.get("action") or "save")
    connection_id = str(data.get("id") or "")
    if action == "delete":
        def delete(store):
            store["connections"] = [item for item in store["connections"] if item["id"] != connection_id]
            for project in store["projects"]:
                if project["connection_id"] == connection_id:
                    project["connection_id"] = ""
        agent_projects.mutate_store(user["id"], delete)
        return jsonify({"ok": True, "deleted": connection_id})
    if action != "save":
        return jsonify({"error": "Unbekannte Aktion."}), 400
    try:
        store = agent_projects.load_store(user["id"])
        existing = next((item for item in store["connections"] if item["id"] == connection_id), None)
        candidate = agent_projects.normalise_connection(data.get("connection"), existing=existing)
    except AgentProjectError as exc:
        return jsonify({"error": str(exc)}), 400

    def save(current):
        entries = current["connections"]
        index = next((position for position, item in enumerate(entries)
                      if item["id"] == candidate["id"]), None)
        if index is None:
            if len(entries) >= agent_projects.MAX_CONNECTIONS:
                raise AgentProjectError("Es sind maximal "
                                        f"{agent_projects.MAX_CONNECTIONS} Verbindungen möglich.")
            entries.append(candidate)
        else:
            entries[index] = candidate

    try:
        agent_projects.mutate_store(user["id"], save)
    except AgentProjectError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True, "connection": _public_connection(candidate)})


@agent_projects_bp.route("/models", methods=["GET"])
def models():
    """Read the live model list of one endpoint — never a cached guess."""
    user, error = _guard()
    if error:
        return error
    connection_id = str(request.args.get("connection_id") or "")
    kind = str(request.args.get("kind") or "")
    base_url = str(request.args.get("base_url") or "")
    if connection_id:
        store = agent_projects.load_store(user["id"])
        try:
            connection = agent_projects.find_connection(store, connection_id)
        except AgentProjectError as exc:
            return jsonify({"error": str(exc)}), 404
        kind, base_url = connection["kind"], connection["base_url"]
    try:
        found = agent_projects.discover_models(kind, base_url)
    except AgentProjectError as exc:
        return jsonify({"error": str(exc)}), 502
    return _no_store({"models": found, "kind": kind, "base_url": base_url})


@agent_projects_bp.route("/folders", methods=["GET"])
def folders():
    user, error = _guard()
    if error:
        return error
    try:
        listing = agent_projects.list_folders(
            str(request.args.get("root_id") or ""), request.args.get("subpath") or "")
    except AgentProjectError as exc:
        return jsonify({"error": str(exc)}), 400
    return _no_store(listing)


@agent_projects_bp.route("/files", methods=["GET"])
def files():
    user, error = _guard()
    if error:
        return error
    try:
        listing = agent_projects.files_for_date(user["id"], str(request.args.get("date") or ""))
    except AgentProjectError as exc:
        return jsonify({"error": str(exc)}), 400
    return _no_store(listing)


@agent_projects_bp.route("/projects", methods=["POST"])
def save_project():
    user, error = _guard(write=True)
    if error:
        return error
    data = request.get_json(silent=True) or {}
    action = str(data.get("action") or "save")
    project_id = str(data.get("id") or "")
    if action == "delete":
        def delete(store):
            store["projects"] = [item for item in store["projects"] if item["id"] != project_id]
        agent_projects.mutate_store(user["id"], delete)
        return jsonify({"ok": True, "deleted": project_id})
    if action != "save":
        return jsonify({"error": "Unbekannte Aktion."}), 400
    store = agent_projects.load_store(user["id"])
    existing = next((item for item in store["projects"] if item["id"] == project_id), None)
    try:
        candidate = agent_projects.normalise_project(
            data.get("project"), store["connections"], existing=existing)
    except AgentProjectError as exc:
        return jsonify({"error": str(exc)}), 400

    def save(current):
        entries = current["projects"]
        index = next((position for position, item in enumerate(entries)
                      if item["id"] == candidate["id"]), None)
        if index is None:
            if len(entries) >= agent_projects.MAX_PROJECTS:
                raise AgentProjectError("Es sind maximal "
                                        f"{agent_projects.MAX_PROJECTS} Projekte möglich.")
            entries.append(candidate)
        else:
            entries[index] = {**entries[index], **candidate}

    try:
        updated, _ = agent_projects.mutate_store(user["id"], save)
    except AgentProjectError as exc:
        return jsonify({"error": str(exc)}), 400
    roots = agent_projects.project_roots()
    project = agent_projects.find_project(updated, candidate["id"])
    return jsonify({"ok": True, "project": _project_view(
        user["id"], project, roots, updated["connections"])})


@agent_projects_bp.route("/<project_id>/submit", methods=["POST"])
def submit(project_id):
    """Queue exactly one host turn for a project; the worker does the spawning."""
    user, error = _guard(write=True)
    if error:
        return error
    data = request.get_json(silent=True) or {}
    store = agent_projects.load_store(user["id"])
    try:
        project = agent_projects.find_project(store, project_id)
        connection = agent_projects.find_connection(store, project["connection_id"])
    except AgentProjectError as exc:
        return jsonify({"error": str(exc)}), 404
    instruction = data.get("instruction")
    if not isinstance(instruction, str):
        return jsonify({"error": "Bitte einen Auftrag formulieren."}), 400
    if project.get("session_id") and agent_project_jobs.active_job(user["id"], project["session_id"]):
        return jsonify({"error": "Für dieses Projekt läuft bereits ein Auftrag."}), 409
    try:
        queued = agent_project_jobs.queue_project_job(
            user["id"], user.get("username") or user["id"], project, connection, instruction)
    except AgentProjectError as exc:
        return jsonify({"error": str(exc)}), 400

    def persist(current):
        try:
            stored = agent_projects.find_project(current, project_id)
        except AgentProjectError:
            return
        stored["session_id"] = queued["session_id"]
        stored["session_path"] = queued["session_path"]
        stored["last_job_id"] = queued["job"]["id"]
        stored["instruction"] = ""

    agent_projects.mutate_store(user["id"], persist)
    main = _main()
    if main:
        main._append_write_ai_history(user["id"], {
            "id": queued["job"]["id"], "at": queued["job"]["created_at"],
            "workflow_tag": queued["job"]["workflow_tag"], "agent": queued["job"]["agent"],
            "model": queued["job"]["model"], "context_type": "agent_project", "status": "queued",
        })
    _enqueue_rebuild(user["id"])
    return jsonify({"ok": True, "queued": True, "job_id": queued["job"]["id"],
                    "session_id": queued["session_id"], "session_path": queued["session_path"],
                    "model": queued["job"]["model"], "status": "queued"}), 202


@agent_projects_bp.route("/<project_id>/turns", methods=["GET"])
def turns(project_id):
    user, error = _guard()
    if error:
        return error
    store = agent_projects.load_store(user["id"])
    try:
        project = agent_projects.find_project(store, project_id)
    except AgentProjectError as exc:
        return jsonify({"error": str(exc)}), 404
    return _no_store(agent_project_jobs.session_turns(user["id"], project))


@agent_projects_bp.route("/<project_id>/session", methods=["POST"])
def reset_session(project_id):
    """Start a fresh agent conversation without losing the stored project."""
    user, error = _guard(write=True)
    if error:
        return error

    def reset(store):
        project = next((item for item in store["projects"] if item["id"] == project_id), None)
        if not project:
            raise AgentProjectError("Das Projekt existiert nicht mehr.")
        if project.get("session_id") and agent_project_jobs.active_job(user["id"], project["session_id"]):
            raise AgentProjectError("Für dieses Projekt läuft noch ein Auftrag.")
        project["session_id"] = ""
        project["session_path"] = ""
        project["last_job_id"] = ""

    try:
        agent_projects.mutate_store(user["id"], reset)
    except AgentProjectError as exc:
        return jsonify({"error": str(exc)}), 409
    return jsonify({"ok": True})


@agent_projects_bp.route("/by-session/<session_id>", methods=["GET"])
def by_session(session_id):
    """Resolve a Brain agent session back to its project so it can be reopened."""
    user, error = _guard()
    if error:
        return error
    project = agent_project_jobs.project_for_session(user["id"], session_id)
    if not project:
        return jsonify({"error": "Zu dieser Session gibt es kein gespeichertes Agent-Projekt."}), 404
    roots = agent_projects.project_roots()
    store = agent_projects.load_store(user["id"])
    return _no_store({"project": _project_view(user["id"], project, roots, store["connections"])})


__all__ = ["agent_projects_bp"]
