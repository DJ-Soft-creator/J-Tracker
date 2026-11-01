"""Create the journal-analysis job, run, prompt and notification schema.

Revision ID: 0011_journal_analysis
Revises: 0010_agent_attachments
Create Date: 2026-09-24

Additive only.  The analysis area is intentionally independent of the
AI-worker/ticket system: it has its own job definition, per-day scan state and
notification queue.  Journals stay file-mastered; only process metadata lives
here, exported to ``<user>/journal-analysen/<slug>/`` for restore.
"""

from alembic import op


revision = "0011_journal_analysis"
down_revision = "0010_agent_attachments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    connection.exec_driver_sql("""
        CREATE TABLE analysis_prompt_templates (
            id uuid PRIMARY KEY,
            owner_id text NOT NULL,
            name text NOT NULL,
            body text NOT NULL,
            version integer NOT NULL DEFAULT 1 CHECK (version >= 1),
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            CONSTRAINT uq_analysis_prompt_templates_owner_name UNIQUE (owner_id, name)
        )
    """)
    connection.exec_driver_sql("""
        CREATE TABLE analysis_jobs (
            id uuid PRIMARY KEY,
            owner_id text NOT NULL,
            slug text NOT NULL,
            name text NOT NULL,
            enabled boolean NOT NULL DEFAULT true,
            schedule_mode text NOT NULL DEFAULT 'daily'
                CHECK (schedule_mode IN ('daily', 'interval', 'manual')),
            schedule_time text NULL,
            schedule_interval_hours integer NULL
                CHECK (schedule_interval_hours IS NULL OR schedule_interval_hours >= 1),
            date_range_mode text NOT NULL
                CHECK (date_range_mode IN ('previous_day', 'current_week', 'last_week',
                                           'current_month', 'last_month', 'custom')),
            custom_start date NULL,
            custom_end date NULL,
            provider_id text NOT NULL DEFAULT '',
            model text NOT NULL DEFAULT '',
            prompt_template_id uuid NULL REFERENCES analysis_prompt_templates(id) ON DELETE RESTRICT,
            output_notification boolean NOT NULL DEFAULT true,
            output_journal boolean NOT NULL DEFAULT false,
            output_family boolean NOT NULL DEFAULT false,
            family_target text NULL,
            extra_sources jsonb NOT NULL DEFAULT '[]'::jsonb,
            summary text NOT NULL DEFAULT '',
            state jsonb NOT NULL DEFAULT '{}'::jsonb,
            job_version integer NOT NULL DEFAULT 1 CHECK (job_version >= 1),
            last_run_date date NULL,
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            CONSTRAINT uq_analysis_jobs_owner_slug UNIQUE (owner_id, slug),
            CONSTRAINT ck_analysis_jobs_family_target CHECK
                (output_family = false OR (family_target IS NOT NULL AND family_target <> '')),
            CONSTRAINT ck_analysis_jobs_custom_range CHECK
                (date_range_mode <> 'custom' OR (custom_start IS NOT NULL AND custom_end IS NOT NULL))
        )
    """)
    connection.exec_driver_sql("""
        CREATE TABLE analysis_runs (
            id uuid PRIMARY KEY,
            job_id uuid NOT NULL REFERENCES analysis_jobs(id) ON DELETE RESTRICT,
            owner_id text NOT NULL,
            run_date date NOT NULL,
            status text NOT NULL CHECK (status IN
                ('queued', 'running', 'completed', 'failed', 'cancelled')),
            job_version integer NOT NULL CHECK (job_version >= 1),
            source_hash char(64) NULL,
            summary text NOT NULL DEFAULT '',
            state jsonb NOT NULL DEFAULT '{}'::jsonb,
            result text NOT NULL DEFAULT '',
            error_code text NULL,
            error_message text NOT NULL DEFAULT '',
            stale boolean NOT NULL DEFAULT false,
            stale_reason text NOT NULL DEFAULT '',
            started_at timestamptz NULL,
            completed_at timestamptz NULL,
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            CONSTRAINT uq_analysis_runs_job_date UNIQUE (job_id, run_date)
        )
    """)
    connection.exec_driver_sql("""
        CREATE TABLE analysis_notifications (
            id uuid PRIMARY KEY,
            owner_id text NOT NULL,
            job_id uuid NOT NULL REFERENCES analysis_jobs(id) ON DELETE RESTRICT,
            run_id uuid NULL REFERENCES analysis_runs(id) ON DELETE RESTRICT,
            run_date date NOT NULL,
            title text NOT NULL DEFAULT '',
            body text NOT NULL DEFAULT '',
            kind text NOT NULL DEFAULT 'analysis'
                CHECK (kind IN ('analysis', 'summary', 'error')),
            status text NOT NULL DEFAULT 'open'
                CHECK (status IN ('open', 'ignored', 'done', 'taken')),
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL
        )
    """)
    connection.exec_driver_sql("""
        CREATE TABLE analysis_run_events (
            id uuid PRIMARY KEY,
            run_id uuid NOT NULL REFERENCES analysis_runs(id) ON DELETE RESTRICT,
            event_type text NOT NULL CHECK (event_type IN
                ('queued', 'started', 'day_completed', 'completed', 'failed',
                 'cancelled', 'stale')),
            detail text NOT NULL DEFAULT '',
            created_at timestamptz NOT NULL
        )
    """)
    connection.exec_driver_sql(
        "CREATE INDEX ix_analysis_jobs_owner ON analysis_jobs (owner_id, updated_at DESC)")
    connection.exec_driver_sql(
        "CREATE INDEX ix_analysis_runs_job_date ON analysis_runs (job_id, run_date DESC)")
    connection.exec_driver_sql(
        "CREATE INDEX ix_analysis_notifications_owner_status "
        "ON analysis_notifications (owner_id, status, created_at DESC)")
    connection.exec_driver_sql(
        "CREATE INDEX ix_analysis_run_events_run ON analysis_run_events (run_id, created_at)")


def downgrade() -> None:
    connection = op.get_bind()
    connection.exec_driver_sql("DROP TABLE IF EXISTS analysis_run_events")
    connection.exec_driver_sql("DROP TABLE IF EXISTS analysis_notifications")
    connection.exec_driver_sql("DROP TABLE IF EXISTS analysis_runs")
    connection.exec_driver_sql("DROP TABLE IF EXISTS analysis_jobs")
    connection.exec_driver_sql("DROP TABLE IF EXISTS analysis_prompt_templates")