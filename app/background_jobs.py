"""Narrow database boundary for Phase-12 background-job leasing.

This module is intentionally not wired into the file-backed Pi worker yet.  It
provides the atomic operations that a later authenticated worker API will use.
"""

from datetime import datetime, timedelta, timezone
import json
import uuid

from sqlalchemy import text

import db


_JOB_KINDS = {"agent_request", "transcription", "maintenance"}
_FINAL_STATUSES = {"completed", "failed", "cancelled", "timed_out"}
_AGENT_ERROR_MESSAGES = {
    "model_no_image_support": (
        "Das konfigurierte Pi-Modell unterstützt keine Bildanhänge. "
        "Bitte für diesen AI-Workflow ein bildfähiges Modell wählen."
    ),
    "model_image_capability_unknown": (
        "Die Bildfähigkeit des konfigurierten Pi-Modells konnte nicht sicher geprüft werden. "
        "Der KI-Auftrag wurde deshalb nicht gestartet."
    ),
}


def agent_error_message(error_code):
    """Return a safe user-facing explanation for a DB worker error code."""
    return _AGENT_ERROR_MESSAGES.get(error_code, error_code)


def _engine(engine=None):
    if engine is not None:
        return engine, False
    from sqlalchemy import create_engine
    return create_engine(db.database_url(), pool_pre_ping=True), True


def _now(now=None):
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("Zeitpunkt muss eine Zeitzone enthalten")
    return now


def _uuid_or_none(value, field):
    if value is None:
        return None
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError(f"Ungültige {field}") from exc


def create_agent_session(owner_id, workflow_tag, agent_name, workflow_prompt, *, model_name="", document_id=None,
                         expected_document_revision_id=None, legacy_relative_path=None,
                         engine=None, now=None):
    """Create a DB-native session without touching a legacy session file."""
    if not isinstance(owner_id, str) or not owner_id or len(owner_id) > 240:
        raise ValueError("Ungültiger Session-Besitzer")
    for value, field in ((workflow_tag, "Workflow-Tag"), (agent_name, "Agent")):
        if not isinstance(value, str) or not value.strip() or len(value) > 240:
            raise ValueError(f"Ungültiger {field}")
    if not isinstance(model_name, str) or len(model_name) > 240:
        raise ValueError("Ungültiges Modell")
    if not isinstance(workflow_prompt, str) or not workflow_prompt.strip() or len(workflow_prompt) > 50000:
        raise ValueError("Ungültiger Workflow-Prompt")
    if legacy_relative_path is not None and (not isinstance(legacy_relative_path, str)
                                             or not legacy_relative_path or len(legacy_relative_path) > 1000):
        raise ValueError("Ungültiger Legacy-Pfad")
    current = _now(now)
    session_id = str(uuid.uuid4())
    active_engine, dispose = _engine(engine)
    try:
        with active_engine.begin() as connection:
            connection.execute(text("""
                INSERT INTO agent_sessions (
                    id, owner_id, document_id, legacy_relative_path, workflow_tag, agent_name, model_name, workflow_prompt,
                    status, expected_document_revision_id, created_at, updated_at
                ) VALUES (
                    CAST(:id AS uuid), :owner_id, CAST(:document_id AS uuid), :legacy_relative_path,
                    :workflow_tag, :agent_name, :model_name, :workflow_prompt, 'active',
                    CAST(:expected_revision AS uuid), :created_at, :created_at
                )
            """), {
                "id": session_id, "owner_id": owner_id, "document_id": _uuid_or_none(document_id, "Dokument-ID"),
                "legacy_relative_path": legacy_relative_path, "workflow_tag": workflow_tag.strip(),
                "agent_name": agent_name.strip(), "model_name": model_name.strip(),
                "workflow_prompt": workflow_prompt.strip(),
                "expected_revision": _uuid_or_none(expected_document_revision_id, "Revisions-ID"),
                "created_at": current,
            })
        return session_id
    finally:
        if dispose:
            active_engine.dispose()


def queue_agent_request(session_id, requested_by_user_id, request_text, idempotency_key, *,
                        submitted_text=None, context_snapshot="", expected_document_revision_id=None,
                        attachment_images=None, max_attempts=1, engine=None, now=None):
    """Durably enqueue one request once, returning the original job on retry."""
    parsed_session_id = _uuid_or_none(session_id, "Session-ID")
    if not isinstance(requested_by_user_id, str) or not requested_by_user_id or len(requested_by_user_id) > 240:
        raise ValueError("Ungültiger anfragender Benutzer")
    if not isinstance(request_text, str) or not request_text.strip() or len(request_text) > 12000:
        raise ValueError("Ungültiger Anfrageinhalt")
    if submitted_text is None:
        submitted_text = request_text
    if not isinstance(submitted_text, str) or not submitted_text.strip() or len(submitted_text) > 200000:
        raise ValueError("Ungültiger eingereichter Text")
    if not isinstance(context_snapshot, str) or len(context_snapshot) > 200000:
        raise ValueError("Ungültiger Kontext-Snapshot")
    attachment_images = attachment_images or []
    if not isinstance(attachment_images, list) or len(attachment_images) > 12:
        raise ValueError("Ungültige KI-Anhänge")
    for item in attachment_images:
        if not isinstance(item, dict) or item.get("type") != "image":
            raise ValueError("Ungültiger KI-Anhang")
        if not all(isinstance(item.get(key), str) and item.get(key) for key in ("path", "mime_type", "sha256")):
            raise ValueError("Ungültiger KI-Anhang")
        if not isinstance(item.get("byte_size"), int) or item["byte_size"] <= 0:
            raise ValueError("Ungültiger KI-Anhang")
    if not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 240:
        raise ValueError("Ungültiger Idempotenzschlüssel")
    if not isinstance(max_attempts, int) or not 1 <= max_attempts <= 10:
        raise ValueError("Ungültige maximale Versuchszahl")
    current = _now(now)
    request_id, job_id = str(uuid.uuid4()), str(uuid.uuid4())
    active_engine, dispose = _engine(engine)
    try:
        with active_engine.begin() as connection:
            session = connection.execute(text("""
                SELECT owner_id, status FROM agent_sessions WHERE id = CAST(:session_id AS uuid) FOR UPDATE
            """), {"session_id": parsed_session_id}).mappings().first()
            if not session:
                raise ValueError("Agent-Session nicht gefunden")
            if session["owner_id"] != requested_by_user_id:
                raise ValueError("Agent-Session gehört nicht zum Benutzer")
            existing = connection.execute(text("""
                SELECT request.id AS request_id, job.id AS job_id
                FROM agent_requests request
                JOIN background_jobs job ON job.agent_request_id = request.id
                WHERE request.session_id = CAST(:session_id AS uuid) AND request.idempotency_key = :key
            """), {"session_id": parsed_session_id, "key": idempotency_key.strip()}).mappings().first()
            if existing:
                return {"request_id": str(existing["request_id"]), "job_id": str(existing["job_id"]), "created": False}
            if session["status"] != "active":
                raise ValueError("Agent-Session nimmt keine neue Anfrage an")
            expected_revision = _uuid_or_none(expected_document_revision_id, "Revisions-ID")
            connection.execute(text("""
                INSERT INTO agent_requests (
                    id, session_id, idempotency_key, requested_by_user_id, request_text, submitted_text, context_snapshot,
                    attachment_images, expected_document_revision_id, status, created_at, updated_at
                ) VALUES (
                    CAST(:id AS uuid), CAST(:session_id AS uuid), :key, :requested_by, :request_text,
                    :submitted_text, :context_snapshot, CAST(:attachment_images AS jsonb),
                    CAST(:expected_revision AS uuid), 'queued', :created_at, :created_at
                )
            """), {
                "id": request_id, "session_id": parsed_session_id, "key": idempotency_key.strip(),
                "requested_by": requested_by_user_id, "request_text": request_text.strip(), "submitted_text": submitted_text,
                "context_snapshot": context_snapshot, "attachment_images": json.dumps(attachment_images),
                "expected_revision": expected_revision, "created_at": current,
            })
            connection.execute(text("""
                INSERT INTO background_jobs (
                    id, owner_id, agent_request_id, job_kind, status, attempt_count, max_attempts, created_at, updated_at
                ) VALUES (
                    CAST(:id AS uuid), :owner_id, CAST(:request_id AS uuid), 'agent_request', 'queued',
                    0, :max_attempts, :created_at, :created_at
                )
            """), {
                "id": job_id, "owner_id": requested_by_user_id, "request_id": request_id,
                "max_attempts": max_attempts, "created_at": current,
            })
            connection.execute(text("""
                INSERT INTO job_events (id, job_id, event_type, detail, created_at)
                VALUES (CAST(:id AS uuid), CAST(:job_id AS uuid), 'queued', 'agent request queued', :created_at)
            """), {"id": str(uuid.uuid4()), "job_id": job_id, "created_at": current})
            connection.execute(text("""
                UPDATE agent_sessions SET status = 'waiting', updated_at = :updated_at
                WHERE id = CAST(:session_id AS uuid)
            """), {"session_id": parsed_session_id, "updated_at": current})
            return {"request_id": request_id, "job_id": job_id, "created": True}
    finally:
        if dispose:
            active_engine.dispose()


def claim_next_job(owner_id, job_kind, *, lease_seconds=300, engine=None, now=None):
    """Atomically lease the oldest queued job for exactly one worker caller."""
    if not isinstance(owner_id, str) or not owner_id or len(owner_id) > 240:
        raise ValueError("Ungültiger Job-Besitzer")
    if job_kind not in _JOB_KINDS:
        raise ValueError("Ungültige Job-Art")
    if not isinstance(lease_seconds, int) or not 1 <= lease_seconds <= 3600:
        raise ValueError("Lease muss zwischen 1 und 3600 Sekunden liegen")
    current = _now(now)
    token = uuid.uuid4()
    expires = current + timedelta(seconds=lease_seconds)
    active_engine, dispose = _engine(engine)
    try:
        with active_engine.begin() as connection:
            job = connection.execute(text("""
                WITH candidate AS (
                    SELECT id FROM background_jobs
                    WHERE owner_id = :owner_id AND job_kind = :job_kind AND status = 'queued'
                    ORDER BY created_at, id
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                UPDATE background_jobs job
                SET status = 'leased', lease_token = CAST(:lease_token AS uuid),
                    lease_expires_at = :lease_expires_at,
                    attempt_count = job.attempt_count + 1, updated_at = :now
                FROM candidate
                WHERE job.id = candidate.id
                RETURNING job.id, job.owner_id, job.agent_request_id, job.job_kind,
                          job.attempt_count, job.lease_expires_at
            """), {
                "owner_id": owner_id, "job_kind": job_kind, "lease_token": str(token),
                "lease_expires_at": expires, "now": current,
            }).mappings().first()
            if not job:
                return None
            connection.execute(text("""
                INSERT INTO job_events (id, job_id, event_type, detail, created_at)
                VALUES (CAST(:id AS uuid), CAST(:job_id AS uuid), 'leased', :detail, :created_at)
            """), {
                "id": str(uuid.uuid4()), "job_id": str(job["id"]),
                "detail": "lease granted", "created_at": current,
            })
            return {**dict(job), "lease_token": str(token)}
    finally:
        if dispose:
            active_engine.dispose()


def claim_next_agent_job(*, lease_seconds=300, engine=None, now=None):
    """Lease one queued agent request without exposing unrelated job kinds."""
    if not isinstance(lease_seconds, int) or not 1 <= lease_seconds <= 3600:
        raise ValueError("Lease muss zwischen 1 und 3600 Sekunden liegen")
    current = _now(now)
    token = uuid.uuid4()
    expires = current + timedelta(seconds=lease_seconds)
    active_engine, dispose = _engine(engine)
    try:
        with active_engine.begin() as connection:
            job = connection.execute(text("""
                WITH candidate AS (
                    SELECT id FROM background_jobs
                    WHERE job_kind = 'agent_request' AND agent_request_id IS NOT NULL AND status = 'queued'
                    ORDER BY created_at, id
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                ), leased AS (
                    UPDATE background_jobs job
                    SET status = 'leased', lease_token = CAST(:lease_token AS uuid),
                        lease_expires_at = :lease_expires_at,
                        attempt_count = job.attempt_count + 1, updated_at = :now
                    FROM candidate
                    WHERE job.id = candidate.id
                    RETURNING job.id, job.agent_request_id, job.owner_id, job.attempt_count, job.lease_expires_at
                )
                SELECT leased.id AS job_id, leased.owner_id, leased.attempt_count, leased.lease_expires_at,
                       request.id AS request_id, request.request_text, request.context_snapshot, request.attachment_images,
                       request.expected_document_revision_id, session.id AS session_id,
                       session.workflow_tag, session.agent_name, session.model_name, session.workflow_prompt
                FROM leased
                JOIN agent_requests request ON request.id = leased.agent_request_id
                JOIN agent_sessions session ON session.id = request.session_id
            """), {
                "lease_token": str(token), "lease_expires_at": expires, "now": current,
            }).mappings().first()
            if not job:
                return None
            connection.execute(text("""
                INSERT INTO job_events (id, job_id, event_type, detail, created_at)
                VALUES (CAST(:id AS uuid), CAST(:job_id AS uuid), 'leased', 'worker lease granted', :created_at)
            """), {"id": str(uuid.uuid4()), "job_id": str(job["job_id"]), "created_at": current})
            return {**dict(job), "lease_token": str(token)}
    finally:
        if dispose:
            active_engine.dispose()


def record_agent_response(job_id, lease_token, response_text, *, engine=None, now=None):
    """Store one proposed response and complete only its exact active lease."""
    parsed_job_id = _uuid_or_none(job_id, "Job-ID")
    parsed_token = _uuid_or_none(lease_token, "Lease-ID")
    if not isinstance(response_text, str) or not response_text.strip() or len(response_text) > 200000:
        raise ValueError("Ungültige Agentenantwort")
    current = _now(now)
    active_engine, dispose = _engine(engine)
    try:
        with active_engine.begin() as connection:
            claimed = connection.execute(text("""
                SELECT job.agent_request_id, request.session_id, request.expected_document_revision_id
                FROM background_jobs job
                JOIN agent_requests request ON request.id = job.agent_request_id
                WHERE job.id = CAST(:job_id AS uuid) AND job.job_kind = 'agent_request'
                  AND job.status = 'leased' AND job.lease_token = CAST(:lease_token AS uuid)
                FOR UPDATE OF job, request
            """), {"job_id": parsed_job_id, "lease_token": parsed_token}).mappings().first()
            if not claimed:
                return None
            response_id = str(uuid.uuid4())
            connection.execute(text("""
                INSERT INTO agent_responses (
                    id, request_id, session_id, response_text, status, expected_document_revision_id, created_at
                ) VALUES (
                    CAST(:id AS uuid), CAST(:request_id AS uuid), CAST(:session_id AS uuid),
                    :response_text, 'proposed', CAST(:expected_revision AS uuid), :created_at
                )
            """), {
                "id": response_id, "request_id": str(claimed["agent_request_id"]),
                "session_id": str(claimed["session_id"]), "response_text": response_text.strip(),
                "expected_revision": _uuid_or_none(claimed["expected_document_revision_id"], "Revisions-ID"),
                "created_at": current,
            })
            connection.execute(text("""
                UPDATE agent_requests SET status = 'completed', completed_at = :now, updated_at = :now
                WHERE id = CAST(:request_id AS uuid)
            """), {"request_id": str(claimed["agent_request_id"]), "now": current})
            connection.execute(text("""
                UPDATE background_jobs
                SET status = 'completed', lease_token = NULL, lease_expires_at = NULL,
                    completed_at = :now, updated_at = :now
                WHERE id = CAST(:job_id AS uuid)
            """), {"job_id": parsed_job_id, "now": current})
            connection.execute(text("""
                UPDATE agent_sessions SET status = 'active', updated_at = :now
                WHERE id = CAST(:session_id AS uuid)
            """), {"session_id": str(claimed["session_id"]), "now": current})
            connection.execute(text("""
                INSERT INTO job_events (id, job_id, event_type, detail, created_at)
                VALUES (CAST(:id AS uuid), CAST(:job_id AS uuid), 'completed', 'response proposed', :created_at)
            """), {"id": str(uuid.uuid4()), "job_id": parsed_job_id, "created_at": current})
            return {"response_id": response_id, "request_id": str(claimed["agent_request_id"]), "status": "proposed"}
    finally:
        if dispose:
            active_engine.dispose()


def agent_job_for_owner(job_id, owner_id, *, engine=None):
    """Read one DB-native agent job without exposing another owner's request."""
    parsed_job_id = _uuid_or_none(job_id, "Job-ID")
    if not isinstance(owner_id, str) or not owner_id or len(owner_id) > 240:
        raise ValueError("Ungültiger Job-Besitzer")
    active_engine, dispose = _engine(engine)
    try:
        with active_engine.connect() as connection:
            row = connection.execute(text("""
                SELECT job.id, job.status, job.created_at, job.lease_expires_at, job.completed_at, job.error_code,
                       request.session_id, request.submitted_text, request.draft_commit_revision,
                       response.response_text, response.status AS response_status
                FROM background_jobs job
                JOIN agent_requests request ON request.id = job.agent_request_id
                LEFT JOIN agent_responses response ON response.request_id = request.id
                WHERE job.id = CAST(:job_id AS uuid) AND job.owner_id = :owner_id AND job.job_kind = 'agent_request'
            """), {"job_id": parsed_job_id, "owner_id": owner_id}).mappings().first()
            return dict(row) if row else None
    finally:
        if dispose:
            active_engine.dispose()


def agent_request_records(owner_id=None, *, engine=None):
    """Return DB-native agent sessions with their requests and responses.

    The optional owner filter is applied in SQL so a user-facing caller never
    reads another user's request content into application memory.
    """
    if owner_id is not None and (not isinstance(owner_id, str) or not owner_id or len(owner_id) > 240):
        raise ValueError("Ungültiger Session-Besitzer")
    where = "WHERE session.owner_id = :owner_id" if owner_id is not None else ""
    active_engine, dispose = _engine(engine)
    try:
        with active_engine.connect() as connection:
            rows = connection.execute(text(f"""
                SELECT session.id AS session_id, session.owner_id, session.workflow_tag,
                       session.agent_name, session.model_name, session.status AS session_status,
                       session.created_at AS session_created_at, session.updated_at AS session_updated_at,
                       request.id AS request_id, request.request_text, request.submitted_text,
                       request.status AS request_status, request.created_at AS request_created_at,
                       request.completed_at AS request_completed_at,
                       job.id AS job_id, job.status AS job_status, job.error_code,
                       job.created_at AS job_created_at, job.completed_at AS job_completed_at,
                       response.response_text, response.status AS response_status,
                       response.created_at AS response_created_at
                FROM agent_sessions session
                LEFT JOIN agent_requests request ON request.session_id = session.id
                LEFT JOIN background_jobs job ON job.agent_request_id = request.id
                LEFT JOIN LATERAL (
                    SELECT value.response_text, value.status, value.created_at
                    FROM agent_responses value
                    WHERE value.request_id = request.id
                    ORDER BY value.created_at DESC, value.id DESC
                    LIMIT 1
                ) response ON true
                {where}
                ORDER BY session.created_at DESC, request.created_at ASC, request.id ASC
            """), {"owner_id": owner_id} if owner_id is not None else {}).mappings().all()
            result = []
            for row in rows:
                item = dict(row)
                for key, value in tuple(item.items()):
                    if isinstance(value, datetime):
                        item[key] = value.isoformat()
                    elif isinstance(value, uuid.UUID):
                        item[key] = str(value)
                result.append(item)
            return result
    finally:
        if dispose:
            active_engine.dispose()


def remember_agent_draft_commit(job_id, owner_id, revision, *, engine=None):
    """Persist the exact draft revision once so retrying the browser call is safe."""
    parsed_job_id = _uuid_or_none(job_id, "Job-ID")
    if not isinstance(revision, str) or len(revision) != 64:
        raise ValueError("Ungültige Entwurfsrevision")
    active_engine, dispose = _engine(engine)
    try:
        with active_engine.begin() as connection:
            connection.execute(text("""
                UPDATE agent_requests request SET draft_commit_revision = :revision, updated_at = now()
                FROM background_jobs job WHERE job.agent_request_id = request.id
                  AND job.id = CAST(:job_id AS uuid) AND job.owner_id = :owner_id
                  AND request.draft_commit_revision IS NULL
            """), {"revision": revision, "job_id": parsed_job_id, "owner_id": owner_id})
    finally:
        if dispose:
            active_engine.dispose()


def finish_job(job_id, lease_token, status, *, error_code=None, engine=None, now=None):
    """Finalize only the exact active lease; stale workers cannot finish a job."""
    if status not in _FINAL_STATUSES:
        raise ValueError("Ungültiger Abschlussstatus")
    try:
        parsed_job_id = uuid.UUID(str(job_id))
        parsed_token = uuid.UUID(str(lease_token))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("Ungültige Job- oder Lease-ID") from exc
    current = _now(now)
    active_engine, dispose = _engine(engine)
    try:
        with active_engine.begin() as connection:
            job = connection.execute(text("""
                UPDATE background_jobs
                SET status = :status, lease_token = NULL, lease_expires_at = NULL,
                    completed_at = :completed_at, updated_at = :completed_at, error_code = :error_code
                WHERE id = CAST(:job_id AS uuid) AND status = 'leased'
                  AND lease_token = CAST(:lease_token AS uuid)
                RETURNING id
            """), {
                "status": status, "job_id": str(parsed_job_id), "lease_token": str(parsed_token),
                "completed_at": current, "error_code": error_code,
            }).scalar()
            if job is None:
                return False
            connection.execute(text("""
                INSERT INTO job_events (id, job_id, event_type, detail, created_at)
                VALUES (CAST(:id AS uuid), CAST(:job_id AS uuid), :event_type, :detail, :created_at)
            """), {
                "id": str(uuid.uuid4()), "job_id": str(job), "event_type": status,
                "detail": error_code or "", "created_at": current,
            })
            return True
    finally:
        if dispose:
            active_engine.dispose()


def recover_expired_leases(*, engine=None, now=None):
    """Return expired claims to the queue; callers can safely run this repeatedly."""
    current = _now(now)
    active_engine, dispose = _engine(engine)
    try:
        with active_engine.begin() as connection:
            jobs = connection.execute(text("""
                UPDATE background_jobs
                SET status = 'queued', lease_token = NULL, lease_expires_at = NULL,
                    updated_at = :now, error_code = 'lease_expired'
                WHERE status = 'leased' AND lease_expires_at <= :now
                RETURNING id
            """), {"now": current}).scalars().all()
            for job_id in jobs:
                connection.execute(text("""
                    INSERT INTO job_events (id, job_id, event_type, detail, created_at)
                    VALUES (CAST(:id AS uuid), CAST(:job_id AS uuid), 'lease_expired', 'lease recovered', :created_at)
                """), {"id": str(uuid.uuid4()), "job_id": str(job_id), "created_at": current})
            return len(jobs)
    finally:
        if dispose:
            active_engine.dispose()
