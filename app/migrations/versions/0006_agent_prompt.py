"""Persist the approved workflow prompt for DB-native agent sessions.

Revision ID: 0006_agent_prompt
Revises: 0005_job_lease_fix
Create Date: 2026-08-12
"""

from alembic import op


revision = "0006_agent_prompt"
down_revision = "0005_job_lease_fix"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    connection.exec_driver_sql("""
        ALTER TABLE agent_sessions
        ADD COLUMN workflow_prompt text NOT NULL DEFAULT ''
    """)
    # Existing 12A rows (there were none in DEV) remain readable.  The check
    # applies to every new DB-native session, so a worker never receives a
    # request with an implicit or guessed prompt.
    connection.exec_driver_sql("""
        ALTER TABLE agent_sessions
        ADD CONSTRAINT ck_agent_sessions_workflow_prompt CHECK (workflow_prompt <> '') NOT VALID
    """)


def downgrade() -> None:
    raise RuntimeError("Die Workflow-Prompt-Migration wird nicht automatisch entfernt; Backup/Restore ist erforderlich.")
