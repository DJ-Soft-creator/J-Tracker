"""Append-only Markdown sessions triggered by configured AI hashtags."""

import json
import os
import re
import uuid
import hashlib
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, jsonify, request

import tagging
from scheduling import path_lock, read_text_file, update_text_file, write_text_file


DATA_DIR = Path(os.environ.get("DATA_DIR", "/app/data"))
ai_sessions_bp = Blueprint("ai_sessions", __name__, url_prefix="/api/ai-sessions")

_TAG_RE = re.compile(r"(?<![\w#])#(ai-[\w-]+)", re.IGNORECASE)
_SESSION_ID_RE = re.compile(r"^[0-9a-f-]{36}$")
_CONFIG_RE = re.compile(r"<!-- jt:ai-session-config\s*(.*?)\s*-->", re.DOTALL)
_DOCUMENT_CONFIG_RE = re.compile(r"<!-- jt:agent-session-config\s*(.*?)\s*-->", re.DOTALL)
_DOCUMENT_EVENT_RE = re.compile(r"<!-- jt:agent-session-event\s+(\{.*?\})\s*-->", re.DOTALL)
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


def _safe_context_files(user_id, paths):
    root = (DATA_DIR / user_id).resolve()
    context = []
    for relative in paths:
        path = root / relative
        try:
            resolved = path.resolve()
            if resolved.relative_to(root) and resolved.is_file() and resolved.suffix == ".md":
                context.append(f"### Datei: {relative}\n{read_text_file(resolved)}")
        except (OSError, ValueError):
            continue
    return "\n\n".join(context)


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
    return next(((tag, workflows[tag]) for tag in tags if tag in workflows), (None, None))


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


def create_sessions_for_entry(user_id, journal_path, entry, now=None):
    """Create one persistent session for each configured AI hashtag in an entry."""
    workflows = tagging.catalog_view(user_id).get("ai", {})
    tags = sorted({tagging.normalise_tag(match.group(1)) for match in _TAG_RE.finditer(entry or "")})
    created = []
    now = now or datetime.now(timezone.utc).astimezone()
    for tag in tags:
        workflow = workflows.get(tag)
        if not workflow:
            continue
        session_id = str(uuid.uuid4())
        filename = f"{tag}-{now.strftime('%Y%m%d-%H%M%S')}-{session_id[:8]}.md"
        path = _user_projects(user_id) / filename
        context_mode = workflow.get("context", "block")
        if context_mode == "journal":
            context = read_text_file(journal_path)
        elif context_mode == "files":
            context = _safe_context_files(user_id, workflow.get("context_files", []))
        else:
            context = entry
        config = {
            "session_id": session_id,
            "workflow_tag": tag,
            "agent": workflow["agent"],
            "model": workflow["model"],
            "prompt": workflow["prompt"],
            "context": context_mode,
            "context_files": workflow.get("context_files", []),
            "source_journal": str(Path(journal_path).relative_to(DATA_DIR / user_id)),
            "created_at": now.isoformat(timespec="seconds"),
        }
        request_body = (
            f"### Auftrag\n{workflow['prompt']}\n\n"
            f"### Auslöser\n{entry.strip()}\n\n"
            f"### Kontext\n{context.strip()}"
        )
        content = (
            "---\n"
            f"id: {session_id}\n"
            "type: ai-session\n"
            "status: active\n"
            f"workflow_tag: {tag}\n"
            f"agent: {workflow['agent']}\n"
            f"model: {workflow['model']}\n"
            f"created_at: {config['created_at']}\n"
            "---\n\n"
            f"# AI Session: #{tag}\n\n"
            f"<!-- jt:ai-session-config\n{json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True)}\n-->\n"
            + _event_block("request", request_body)
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        write_text_file(path, content)
        created.append({"session_id": session_id, "path": f"projects/{filename}", "tag": tag})
    if created:
        _enqueue_rebuild(user_id)
    return created


def parse_session(content, relative_path=""):
    config_match = _CONFIG_RE.search(content or "")
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
    return {
        **config,
        "archived": "/_Archive/" in f"/{relative_path}",
        "events": list(reversed(events)),
    }


def _find_session(user_id, session_id):
    if not isinstance(session_id, str) or not _SESSION_ID_RE.fullmatch(session_id):
        return None, None
    root = _user_projects(user_id)
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
    _append_session_event(path, "user-reply", text, user_id=user["id"])
    _enqueue_rebuild(user["id"])
    return jsonify({"ok": True})


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
    _append_session_event(path, "archived", "Session abgeschlossen und archiviert.", user_id=user["id"])
    archive_dir = _user_projects(user["id"]) / "_Archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    target = archive_dir / path.name
    with path_lock(path, exclusive=True):
        if target.exists():
            return jsonify({"error": "Archive target already exists"}), 409
        os.replace(path, target)
    _enqueue_rebuild(user["id"])
    return jsonify({"ok": True, "path": f"projects/_Archive/{target.name}"})
