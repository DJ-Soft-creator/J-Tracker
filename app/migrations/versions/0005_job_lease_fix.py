"""Make the background-job lease invariant NULL-safe.

Revision ID: 0005_job_lease_fix
Revises: 0004_agent_jobs
Create Date: 2026-08-12
"""

from alembic import op


revision = "0005_job_lease_fix"
down_revision = "0004_agent_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    # 0004 assigned this name explicitly.  A direct DDL statement also avoids
    # psycopg mistaking PostgreSQL's format-string percent tokens for binds.
    connection.exec_driver_sql("ALTER TABLE background_jobs DROP CONSTRAINT ck_background_jobs_lease")
    # 0004 did not yet enforce this invariant when one of the two nullable
    # lease fields was set.  A non-leased job has no active worker claim;
    # clearing such a partial claim is therefore lossless.  An incomplete
    # leased claim cannot be resumed safely and returns to its durable queue.
    connection.exec_driver_sql("""
        UPDATE background_jobs
        SET lease_token = NULL, lease_expires_at = NULL
        WHERE status <> 'leased'
          AND (lease_token IS NOT NULL OR lease_expires_at IS NOT NULL)
    """)
    connection.exec_driver_sql("""
        UPDATE background_jobs
        SET status = 'queued', lease_token = NULL, lease_expires_at = NULL,
            updated_at = now(), error_code = COALESCE(error_code, 'incomplete_lease_recovered')
        WHERE status = 'leased'
          AND (lease_token IS NULL OR lease_expires_at IS NULL)
    """)
    connection.exec_driver_sql("""
        ALTER TABLE background_jobs
        ADD CONSTRAINT ck_background_jobs_lease CHECK (
            (status = 'leased' AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)
            OR (status <> 'leased' AND lease_token IS NULL AND lease_expires_at IS NULL)
        )
    """)


def downgrade() -> None:
    raise RuntimeError("Die Lease-Constraint-Migration wird nicht automatisch entfernt; Backup/Restore ist erforderlich.")
