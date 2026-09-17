"""Create the Phase-12 agent-session and background-job foundation.

Revision ID: 0004_agent_jobs
Revises: 0003_media_references
Create Date: 2026-08-12

This is additive only.  Existing Markdown markers and file-backed Pi jobs remain
the active implementation until a later, separately gated cutover.
"""

from alembic import op


revision = "0004_agent_jobs"
down_revision = "0003_media_references"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    connection.exec_driver_sql("""
        CREATE TABLE agent_sessions (
            id uuid PRIMARY KEY,
            owner_id text NOT NULL,
            document_id uuid NULL REFERENCES documents(id) ON DELETE RESTRICT,
            legacy_relative_path text NULL,
            workflow_tag text NOT NULL,
            agent_name text NOT NULL,
            model_name text NOT NULL DEFAULT '',
            status text NOT NULL CHECK (status IN
                ('active', 'paused', 'waiting', 'running', 'ended', 'archived', 'conflict', 'error')),
            expected_document_revision_id uuid NULL REFERENCES document_revisions(id) ON DELETE RESTRICT,
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            ended_at timestamptz NULL,
            CONSTRAINT ck_agent_sessions_legacy_path CHECK
                (legacy_relative_path IS NULL OR (legacy_relative_path <> '' AND legacy_relative_path !~ '(^/|(^|/)\\.\\.(/|$))'))
        )
    """)
    connection.exec_driver_sql("""
        CREATE TABLE agent_requests (
            id uuid PRIMARY KEY,
            session_id uuid NOT NULL REFERENCES agent_sessions(id) ON DELETE RESTRICT,
            idempotency_key text NOT NULL,
            requested_by_user_id text NOT NULL,
            request_text text NOT NULL,
            context_snapshot text NOT NULL DEFAULT '',
            expected_document_revision_id uuid NULL REFERENCES document_revisions(id) ON DELETE RESTRICT,
            status text NOT NULL CHECK (status IN ('queued', 'leased', 'completed', 'failed', 'cancelled', 'timed_out')),
            attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            completed_at timestamptz NULL,
            CONSTRAINT uq_agent_requests_session_idempotency UNIQUE (session_id, idempotency_key)
        )
    """)
    connection.exec_driver_sql("""
        CREATE TABLE agent_permissions (
            id uuid PRIMARY KEY,
            session_id uuid NOT NULL REFERENCES agent_sessions(id) ON DELETE RESTRICT,
            request_id uuid NULL REFERENCES agent_requests(id) ON DELETE RESTRICT,
            permission_key text NOT NULL,
            scope_description text NOT NULL DEFAULT '',
            status text NOT NULL CHECK (status IN ('pending', 'allowed', 'denied', 'revoked')),
            requested_at timestamptz NOT NULL,
            decided_at timestamptz NULL,
            decided_by_user_id text NULL,
            CONSTRAINT uq_agent_permissions_session_key UNIQUE (session_id, permission_key)
        )
    """)
    connection.exec_driver_sql("""
        CREATE TABLE background_jobs (
            id uuid PRIMARY KEY,
            owner_id text NOT NULL,
            agent_request_id uuid NULL UNIQUE REFERENCES agent_requests(id) ON DELETE RESTRICT,
            job_kind text NOT NULL CHECK (job_kind IN ('agent_request', 'transcription', 'maintenance')),
            status text NOT NULL CHECK (status IN ('queued', 'leased', 'completed', 'failed', 'cancelled', 'timed_out')),
            lease_token uuid NULL,
            lease_expires_at timestamptz NULL,
            attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
            max_attempts integer NOT NULL DEFAULT 1 CHECK (max_attempts >= 1),
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            completed_at timestamptz NULL,
            error_code text NULL,
            CONSTRAINT ck_background_jobs_lease CHECK (
                (status = 'leased' AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)
                OR (status <> 'leased' AND lease_token IS NULL AND lease_expires_at IS NULL)
            )
        )
    """)
    connection.exec_driver_sql("""
        CREATE TABLE agent_responses (
            id uuid PRIMARY KEY,
            request_id uuid NOT NULL UNIQUE REFERENCES agent_requests(id) ON DELETE RESTRICT,
            session_id uuid NOT NULL REFERENCES agent_sessions(id) ON DELETE RESTRICT,
            response_text text NOT NULL,
            status text NOT NULL CHECK (status IN ('proposed', 'applied', 'rejected', 'conflict', 'error')),
            expected_document_revision_id uuid NULL REFERENCES document_revisions(id) ON DELETE RESTRICT,
            applied_document_revision_id uuid NULL REFERENCES document_revisions(id) ON DELETE RESTRICT,
            created_at timestamptz NOT NULL,
            decided_at timestamptz NULL,
            decided_by_user_id text NULL
        )
    """)
    connection.exec_driver_sql("""
        CREATE TABLE job_events (
            id uuid PRIMARY KEY,
            job_id uuid NOT NULL REFERENCES background_jobs(id) ON DELETE RESTRICT,
            event_type text NOT NULL CHECK (event_type IN
                ('queued', 'leased', 'lease_expired', 'retry_scheduled', 'completed', 'failed', 'cancelled', 'timed_out')),
            detail text NOT NULL DEFAULT '',
            created_at timestamptz NOT NULL
        )
    """)
    connection.exec_driver_sql("CREATE INDEX ix_agent_sessions_owner_updated ON agent_sessions (owner_id, updated_at DESC)")
    connection.exec_driver_sql("CREATE INDEX ix_agent_requests_session_created ON agent_requests (session_id, created_at DESC)")
    connection.exec_driver_sql("CREATE INDEX ix_background_jobs_claim ON background_jobs (created_at) WHERE status = 'queued'")
    connection.exec_driver_sql("CREATE INDEX ix_background_jobs_lease_expiry ON background_jobs (lease_expires_at) WHERE status = 'leased'")
    connection.exec_driver_sql("CREATE INDEX ix_job_events_job_created ON job_events (job_id, created_at)")


def downgrade() -> None:
    raise RuntimeError("Die Agentenjob-Migration wird nicht automatisch entfernt; Backup/Restore ist erforderlich.")
