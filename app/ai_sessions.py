"""Append-only Markdown sessions triggered by configured AI hashtags."""

import json
import logging
import os
import re
import uuid
import hashlib
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, jsonify, request
from sqlalchemy.exc import SQLAlchemyError

import background_jobs
import tagging
from db import agent_jobs_enabled
from scheduling import path_lock, read_text_file, update_text_file


DATA_DIR = Path(os.environ.get("DATA_DIR", "/app/data"))
ai_sessions_bp = Blueprint("ai_sessions", __name__, url_prefix="/api/ai-sessions")
logger = logging.getLogger(__name__)

_TAG_RE = re.compile(r"(?<![\w#])#(ai-[\w-]+)", re.IGNORECASE)
_SESSION_ID_RE = re.compile(r"^[0-9a-f-]{36}$")
_CONFIG_RE = re.compile(r"<!-- jt:ai-session-config\s*(.*?)\s*-->", re.DOTALL)
_DOCUMENT_CONFIG_RE = re.compile(r"<!-- jt:agent-session-config\s*(.*?)\s*-->", re.DOTALL)
_DOCUMENT_EVENT_RE = re.compile(r"<!-- jt:agent-session-event\s+(\{.*?\})\s*-->", re.DOTALL)
_DOCUMENT_EVENT_BLOCK_RE = re.compile(
    r"<!-- jt:agent-session-event\s+(\{.*?\})\s*-->\s*\n(.*?)(?=\n___\s*(?:\n|$)|\Z)",
    re.DOTALL,
)
_AGENT_ANSWER_RE = re.compile(
    r"<!-- jt:agent-session\s+(\{.*?\})\s*-->\s*\n(.*?)(?=\n___\s*(?:\n|$)|\Z)",
    re.DOTALL,
)
_EVENT_RE = re.compile(
    r"<!-- jt:ai-session-event\s+(\{.*?\})\s*-->\n(.*?)(?=\n___\s*(?:\n|$)|\Z)",
    re.DOTALL,
)


def _main_module():
    import sys

    return sys.modules.get("main") or sys.modules.get("__main__")


def _current_user():
    main = _main_module()
    return main._current_user() if main else None


def _csrf_error():
    main = _main_module()
    return main.csrf_protect(lambda: None)() if main else (jsonify({"error": "Unauthorized"}), 401)


def _enqueue_rebuild(user_id):
    main = _main_module()
    if main:
        main.brain_module.enqueue_rebuild(user_id)


def _user_projects(user_id):
    return DATA_DIR / user_id / "projects"


def _user_agent_sessions(user_id):
    return DATA_DIR / user_id / "ai-agent"


def _event_block(event_type, body, **metadata):
    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    event = {"id": str(uuid.uuid4()), "type": event_type, "at": now, **metadata}
    return (
        "\n___\n\n"
        f"## {event_type.replace('-', ' ').title()} | Datum & Uhrzeit: {now}\n"
        f"<!-- jt:ai-session-event {json.dumps(event, ensure_ascii=False, sort_keys=True)} -->\n"
        f"{str(body).strip()}\n\n"
        "___\n"
    )


def _validated_document_context_files(user_id, paths):
    """Validate, rather than read, files handed to the external monitor.

    A symlink is rejected even when it currently happens to resolve inside the
    user root: otherwise a later target swap would turn the persisted config into
    an authorisation bypass.
    """
    root = (DATA_DIR / user_id).resolve()
    validated = []
    for relative in paths or []:
        if not isinstance(relative, str) or not relative or len(relative) > 240:
            return None
        candidate = root / relative
        try:
            if candidate.is_symlink() or not candidate.is_file() or candidate.suffix.lower() != ".md":
                return None
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(root)
        except (OSError, ValueError):
            return None
        validated.append(relative)
    return validated


def _document_config(content):
    """Return the one supported in-document session config, or a repair state.

    This deliberately does not accept the old project-session marker: old session
    files remain readable through ``parse_session`` but are never reinterpreted as
    document sessions.
    """
    matches = list(_DOCUMENT_CONFIG_RE.finditer(content or ""))
    if not matches:
        return None, None
    if len(matches) != 1:
        return None, "multiple agent session configurations"
    try:
        config = json.loads(matches[0].group(1))
    except json.JSONDecodeError:
        return None, "invalid agent session configuration"
    if not isinstance(config, dict) or not _SESSION_ID_RE.fullmatch(str(config.get("session_id", ""))):
        return None, "invalid agent session identifier"
    if config.get("status") not in {"active", "paused", "ended", "waiting", "running", "conflict", "error"}:
        return None, "invalid agent session status"
    return config, None


def document_session_status(content):
    """Small, safe-to-expose status object for a source Markdown document."""
    config, error = _document_config(content)
    if error:
        return {"state": "repair", "error": error}
    if not config:
        return None
    events = []
    for match in _DOCUMENT_EVENT_RE.finditer(content):
        try:
            event = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return {
        "session_id": config["session_id"], "status": config["status"],
        "workflow_tag": config.get("workflow_tag", ""), "agent": config.get("agent", ""),
        "updated_at": events[-1].get("at", config.get("created_at", "")) if events else config.get("created_at", ""),
        "repair": False,
    }


def _document_event(event_type, body, **metadata):
    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    event = {"schema": 1, "id": str(uuid.uuid4()), "type": event_type, "at": now, **metadata}
    return (
        "\n\n___\n\n"
        f"## {event_type.replace('-', ' ').title()} | Datum & Uhrzeit: {now}\n"
        f"<!-- jt:agent-session-event {json.dumps(event, ensure_ascii=False, sort_keys=True)} -->\n\n"
        f"{body.strip()}\n\n___\n"
    )


def _insert_before_footer(content, addition):
    footer = '<!-- jt:hashtag-index:start schema="1" -->'
    position = content.find(footer)
    if position >= 0:
        return content[:position].rstrip() + addition + "\n" + content[position:]
    return content.rstrip() + addition


def _workflow_for_tags(user_id, content):
    workflows = tagging.catalog_view(user_id).get("ai", {})
    tags = sorted({tagging.normalise_tag(match.group(1)) for match in _TAG_RE.finditer(content or "")})
    return next((
        (tag, workflows[tag]) for tag in tags
        if tag in workflows and workflows[tag].get("target") != "daily_journal"
    ), (None, None))


def prepare_document_save(user_id, content, previous_content, *, is_journal=False, actor_id=None):
    """Add durable in-document session metadata only during an explicit save.

    ``previous_content`` is the authenticated source read while holding its lock.
    Consequently an agent-originated revision is never turned into another turn by
    this web path, and a no-op save never queues work.
    """
    config, error = _document_config(previous_content)
    if error:
        # Preserve malformed hidden metadata for a deliberate repair/end action.
        return content, document_session_status(previous_content)
    changed = content != previous_content
    if config and _DOCUMENT_CONFIG_RE.search(content) is None:
        # The regular editor must not silently destroy session control metadata.
        content = _DOCUMENT_CONFIG_RE.sub(lambda match: match.group(0) + "\n", previous_content, count=1).rstrip() + "\n\n" + content.lstrip()
        changed = True
    if not config:
        tag, workflow = _workflow_for_tags(user_id, content)
        if not workflow:
            return content, None
        context_files = _validated_document_context_files(user_id, workflow.get("context_files", []))
        if context_files is None:
            return content, {"state": "error", "error": "One or more context files are not authorised"}
        context = str(workflow.get("context", "section")).lower()
        context = {"block": "section", "files": "none"}.get(context, context)
        if context == "journal" and not is_journal:
            return content, {"state": "error", "error": "journal context is only allowed for journal files"}
        config = {
            "schema": 1, "session_id": str(uuid.uuid4()), "status": "active",
            "workflow_tag": tag, "agent": workflow["agent"], "model": workflow["model"],
            "prompt": workflow["prompt"], "context": context,
            "context_files": context_files, "actor_id": actor_id or user_id,
            "created_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "source_revision": "pending",
        }
        marker = "<!-- jt:agent-session-config\n" + json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True) + "\n-->\n"
        content = marker + "\n" + content.lstrip()
        changed = True
        content = _insert_before_footer(content, _document_event(
            "user-request", "Neue Session aus einer explizit gespeicherten Dokumentänderung.",
            user_id=actor_id or user_id,
        ))
    elif changed and config.get("status") == "active":
        # Agent output has an origin marker and is written by the host, not this
        # route. A user save gets exactly one immutable request event.
        content = _insert_before_footer(content, _document_event(
            "user-request", "Explizit gespeicherte Dokumentänderung.", user_id=actor_id or user_id,
        ))
    if config and changed:
        # Revision is calculated without depending on its own serialized value.
        revision = hashlib.sha256(re.sub(r'"source_revision":\s*"[^"]*"', '"source_revision":""', content).encode("utf-8")).hexdigest()
        content = re.sub(r'("source_revision"\s*:\s*")[^"]*(")', r'\g<1>' + revision + r'\g<2>', content, count=1)
    return content, document_session_status(content)


def set_document_session_status(content, action, user_id):
    """Apply an explicit pause/resume/end action without accepting a file path."""
    config, error = _document_config(content)
    if error or not config:
        return content, {"state": "repair", "error": error or "No agent session"}
    target = {"pause": "paused", "resume": "active", "end": "ended", "update": "active"}.get(action)
    if not target:
        raise ValueError("Unknown session action")
    if action == "update" and config.get("status") != "active":
        raise ValueError("Only active sessions can be updated")
    config["status"] = target
    config["updated_at"] = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    marker = "<!-- jt:agent-session-config\n" + json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True) + "\n-->"
    updated = _DOCUMENT_CONFIG_RE.sub(marker, content, count=1)
    event_type = "user-request" if action == "update" else "session-" + action
    body = "Explizite Session-Aktualisierung" if action == "update" else target
    updated = _insert_before_footer(updated, _document_event(event_type, body, user_id=user_id))
    return updated, document_session_status(updated)


def parse_session(content, relative_path=""):
    config_match = _CONFIG_RE.search(content or "") or _DOCUMENT_CONFIG_RE.search(content or "")
    if not config_match:
        return None
    try:
        config = json.loads(config_match.group(1))
    except json.JSONDecodeError:
        return None
    if not isinstance(config, dict) or not _SESSION_ID_RE.fullmatch(str(config.get("session_id", ""))):
        return None
    events = []
    for match in _EVENT_RE.finditer(content or ""):
        try:
            metadata = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(metadata, dict):
            events.append({**metadata, "body": match.group(2).strip()})
    for pattern, default_type in ((_DOCUMENT_EVENT_BLOCK_RE, "user-request"), (_AGENT_ANSWER_RE, "agent-answer")):
        for match in pattern.finditer(content or ""):
            try:
                metadata = json.loads(match.group(1))
            except json.JSONDecodeError:
                continue
            if isinstance(metadata, dict):
                events.append({"type": default_type, **metadata, "body": match.group(2).strip()})
    events.sort(key=lambda event: str(event.get("at", "")))
    return {
        **config,
        "archived": "/_Archive/" in f"/{relative_path}",
        "events": list(reversed(events)),
    }


def _find_session(user_id, session_id):
    if not isinstance(session_id, str) or not _SESSION_ID_RE.fullmatch(session_id):
        return None, None
    for root in (_user_agent_sessions(user_id), _user_projects(user_id)):
        for directory in (root, root / "_Archive"):
            if not directory.is_dir():
                continue
            for path in directory.glob("*.md"):
                content = read_text_file(path)
                session = parse_session(content, path.relative_to(root).as_posix())
                if session and session["session_id"] == session_id:
                    return path, session
    return None, None


def _append_session_event(path, event_type, body, **metadata):
    update_text_file(path, lambda current: current.rstrip() + "\n" + _event_block(event_type, body, **metadata))


def _active_session_job(user_id, session_id):
    jobs_dir = DATA_DIR / user_id / "ai_jobs"
    for job_path in jobs_dir.glob("*.json") if jobs_dir.is_dir() else []:
        try:
            job = json.loads(read_text_file(job_path))
        except (OSError, json.JSONDecodeError):
            continue
        if job.get("session_id") == session_id and job.get("status") in {"queued", "running", "cancelling", "apply_requested"}:
            return job
    return None


@ai_sessions_bp.route("/audit", methods=["GET"])
def audit_overview():
    """Admin-only, cross-user view of every persisted Pi request."""
    user = _current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    if user.get("admin") is not True:
        return jsonify({"error": "Admin access is required"}), 403
    main = _main_module()
    users = {
        item.get("id"): item.get("username")
        for item in (main._read_users_file().get("users", []) if main else [])
        if isinstance(item, dict)
    }
    entries = []
    for job_path in DATA_DIR.glob("*/ai_jobs/*.json"):
        if not re.fullmatch(r"[0-9a-f-]{36}\.json", job_path.name):
            continue
        try:
            job = json.loads(read_text_file(job_path))
        except (OSError, json.JSONDecodeError):
            continue
        owner_id = job_path.parent.parent.name
        if job.get("user_id") != owner_id:
            continue
        response = None
        if main:
            current = main._pi_job_response(owner_id, job.get("id"))
            response = current.get("response") if current else None
        entries.append({
            "id": job.get("id"), "session_id": job.get("session_id"),
            "user_id": owner_id, "username": job.get("username") or users.get(owner_id) or "",
            "created_at": job.get("created_at"), "started_at": job.get("started_at"),
            "completed_at": job.get("completed_at"), "status": job.get("status"),
            "workflow_tag": job.get("workflow_tag"), "agent": job.get("agent"),
            "model": job.get("model"), "delivery": job.get("delivery", "interactive"),
            "context_type": job.get("context_type"),
            "request": job.get("submitted_text", job.get("user_request", "")),
            "agent_request": job.get("user_request", ""),
            "response": response, "error": job.get("error"), "source_path": job.get("source_path"),
            "knowledge_tags": [item.get("tag") for item in job.get("knowledge_snapshots", []) if isinstance(item, dict)]
                + [item.get("tag") for item in job.get("knowledge_references", []) if isinstance(item, dict)],
        })
    if agent_jobs_enabled():
        try:
            for record in background_jobs.agent_request_records():
                if not record.get("job_id"):
                    continue
                owner_id = record.get("owner_id") or ""
                entries.append({
                    "id": record.get("job_id"), "session_id": record.get("session_id"),
                    "user_id": owner_id, "username": users.get(owner_id) or "",
                    "created_at": record.get("job_created_at") or record.get("request_created_at"),
                    "started_at": None, "completed_at": record.get("job_completed_at"),
                    "status": record.get("job_status"), "workflow_tag": record.get("workflow_tag"),
                    "agent": record.get("agent_name"), "model": record.get("model_name"),
                    "delivery": "background-db", "context_type": None,
                    "request": record.get("submitted_text") or record.get("request_text") or "",
                    "agent_request": record.get("request_text") or "",
                    "response": record.get("response_text"),
                    "error": background_jobs.agent_error_message(record.get("error_code")),
                    "source_path": f"DB-Agent/{record.get('session_id')}", "knowledge_tags": [],
                    "db_native": True,
                })
        except (SQLAlchemyError, RuntimeError, ValueError):
            logger.exception("DB-native agent audit could not be loaded")
    entries.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    query = str(request.args.get("q") or "").strip().casefold()
    if query:
        entries = [item for item in entries if any(
            query in str(item.get(key) or "").casefold()
            for key in ("id", "session_id", "user_id", "username", "workflow_tag", "status", "request", "agent_request", "response", "error")
        )]
    try:
        offset = max(0, int(request.args.get("offset", "0")))
        limit = min(200, max(1, int(request.args.get("limit", "100"))))
    except ValueError:
        return jsonify({"error": "offset/limit must be integers"}), 400
    total = len(entries)
    response = jsonify({
        "entries": entries[offset:offset + limit], "total": total,
        "offset": offset, "limit": limit, "admin_only": True,
    })
    response.headers["Cache-Control"] = "no-store"
    return response


@ai_sessions_bp.route("/<session_id>/reply", methods=["POST"])
def reply(session_id):
    user = _current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    csrf_error = _csrf_error()
    if csrf_error:
        return csrf_error
    path, session = _find_session(user["id"], session_id)
    if not path or session.get("archived"):
        return jsonify({"error": "Session not found"}), 404
    data = request.get_json(silent=True) or {}
    text = str(data.get("text") or "").strip()
    if not text or len(text) > 12000:
        return jsonify({"error": "A reply between 1 and 12000 characters is required"}), 400
    if _active_session_job(user["id"], session_id):
        return jsonify({"error": "Für diese Session läuft bereits ein Auftrag."}), 409
    main = _main_module()
    workflow_tag = tagging.normalise_tag(session.get("workflow_tag", ""))
    workflow = tagging.catalog_view(user["id"]).get("ai", {}).get(workflow_tag)
    if not main or not workflow or workflow.get("agent") != "pi":
        return jsonify({"error": "Der Pi-Workflow dieser Session ist nicht mehr verfügbar."}), 409
    try:
        snapshots, references = main._knowledge_snapshots_for_write(user["id"], text)
        write_target = main._write_target_snapshot_for_write(user["id"], text)
        user_request = main._write_ai_user_request(text, workflow_tag, snapshots, references)
        if write_target:
            user_request = re.sub(
                r"(?<![\w#])#" + re.escape(write_target["tag"]) + r"\b", "", user_request,
                flags=re.UNICODE,
            ).strip()
        if not user_request:
            return jsonify({"error": "Bitte einen Folgeauftrag formulieren."}), 400
        job = main._queue_pi_write_job(
            user["id"], user["username"], workflow_tag, workflow, "session", text,
            user_request, "", snapshots, references, write_target,
            session_id=session_id, session_path=path, delivery="follow_up",
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    _enqueue_rebuild(user["id"])
    return jsonify({"ok": True, "queued": True, "job_id": job["id"], "session_id": session_id}), 202


@ai_sessions_bp.route("/<session_id>/permission", methods=["POST"])
def permission(session_id):
    user = _current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    csrf_error = _csrf_error()
    if csrf_error:
        return csrf_error
    path, session = _find_session(user["id"], session_id)
    if not path or session.get("archived"):
        return jsonify({"error": "Session not found"}), 404
    data = request.get_json(silent=True) or {}
    decision = data.get("decision")
    permission_id = str(data.get("permission_id") or "").strip()
    if decision not in {"allow", "deny"} or not permission_id or len(permission_id) > 120:
        return jsonify({"error": "A valid permission decision is required"}), 400
    _append_session_event(
        path, "permission-decision", "Erlaubt" if decision == "allow" else "Abgelehnt",
        permission_id=permission_id, decision=decision, user_id=user["id"],
    )
    _enqueue_rebuild(user["id"])
    return jsonify({"ok": True})


@ai_sessions_bp.route("/<session_id>/archive", methods=["POST"])
def archive(session_id):
    user = _current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    csrf_error = _csrf_error()
    if csrf_error:
        return csrf_error
    path, session = _find_session(user["id"], session_id)
    if not path or session.get("archived"):
        return jsonify({"error": "Session not found"}), 404
    if _active_session_job(user["id"], session_id):
        return jsonify({"error": "Eine laufende Session kann nicht archiviert werden."}), 409
    _append_session_event(path, "archived", "Session abgeschlossen und archiviert.", user_id=user["id"])
    root = _user_agent_sessions(user["id"]) if path.parent.name == "ai-agent" else _user_projects(user["id"])
    archive_dir = root / "_Archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    target = archive_dir / path.name
    with path_lock(path, exclusive=True):
        if target.exists():
            return jsonify({"error": "Archive target already exists"}), 409
        os.replace(path, target)
    _enqueue_rebuild(user["id"])
    return jsonify({"ok": True, "path": f"{root.name}/_Archive/{target.name}"})
