"""Combined legacy-file and DB-native agent sessions for the Brain UI."""

import logging
import sys

from sqlalchemy.exc import SQLAlchemyError

import background_jobs
from db import agent_jobs_enabled


_brain = sys.modules.get("brain")
if _brain is None:  # pragma: no cover - enforced by brain.py import order
    raise RuntimeError("brain facade must exist before domain modules are imported")

logger = logging.getLogger(__name__)


def _sync_from_facade():
    pass


def _db_session_files(user_id, query, start, end, selected_tags, excluded_tags=None, any_tags=None):
    try:
        records = background_jobs.agent_request_records(user_id)
    except (SQLAlchemyError, RuntimeError, ValueError):
        logger.exception("DB-native agent sessions could not be loaded for %s", user_id)
        return []
    required, excluded, _ = _brain._query_terms(query)
    selected_tags = selected_tags or set()
    excluded_tags = excluded_tags or set()
    any_tags = any_tags or set()
    sessions = {}
    for record in records:
        session_id = record["session_id"]
        session = sessions.setdefault(session_id, {
            "session_id": session_id,
            "workflow_tag": record.get("workflow_tag") or "",
            "agent": record.get("agent_name") or "",
            "model": record.get("model_name") or "",
            "status": record.get("session_status") or "active",
            "latest_job_status": record.get("job_status") or "",
            "created_at": record.get("session_created_at") or "",
            "updated_at": record.get("session_updated_at") or record.get("session_created_at") or "",
            "archived": False, "db_native": True, "read_only": True, "events": [],
        })
        if record.get("job_status"):
            session["latest_job_status"] = record["job_status"]
            session["updated_at"] = record.get("job_completed_at") or record.get("job_created_at") or session["updated_at"]
        if record.get("request_id"):
            session["events"].append({
                "type": "user-request", "at": record.get("request_created_at") or "",
                "body": record.get("submitted_text") or record.get("request_text") or "",
            })
        if record.get("response_text"):
            session["events"].append({
                "type": "agent-answer",
                "at": record.get("response_created_at") or record.get("job_completed_at") or "",
                "body": record["response_text"],
            })
        elif record.get("error_code"):
            session["events"].append({
                "type": "error", "at": record.get("job_completed_at") or record.get("job_created_at") or "",
                "body": record["error_code"],
            })
    files = []
    for session in sessions.values():
        session["events"].sort(key=lambda event: str(event.get("at") or ""), reverse=True)
        workflow_tag = _brain._normalise(session["workflow_tag"])
        if not _brain._tags_pass({workflow_tag}, selected_tags, excluded_tags, any_tags):
            continue
        haystack = _brain._normalise("\n".join([
            session["session_id"], session["workflow_tag"], session["agent"], session["model"],
            *(str(event.get("body") or "") for event in session["events"]),
        ]))
        if not all(term in haystack for term in required) or any(term in haystack for term in excluded):
            continue
        if not _brain._in_date_range(session["created_at"], start, end):
            continue
        files.append({
            "doc_id": f"db-agent:{session['session_id']}", "kind": "agent", "source": "personal",
            "source_label": "Privat", "path": f"DB-Agent/{session['session_id']}",
            "title": f"#{session['workflow_tag']}", "modified_at": session["updated_at"],
            "tags": [], "ai_session": session,
        })
    return files


def _personal_agent_sessions(user_id, query="", start=None, end=None, selected_tags=None, excluded_tags=None,
                             any_tags=None):
    files = _brain._personal_files(user_id, "agent", query, start, end, selected_tags, excluded_tags, any_tags)
    if agent_jobs_enabled():
        files.extend(_db_session_files(user_id, query, start, end, selected_tags, excluded_tags, any_tags))
    return sorted(files, key=lambda item: str(item.get("modified_at") or ""), reverse=True)


__all__ = ["_personal_agent_sessions"]
