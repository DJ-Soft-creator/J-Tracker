"""Store the exact approved browser submission for direct draft insertion.

Revision ID: 0007_agent_submitted_text
Revises: 0006_agent_prompt
"""

from alembic import op


revision = "0007_agent_submitted_text"
down_revision = "0006_agent_prompt"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE agent_requests ADD COLUMN submitted_text text NOT NULL DEFAULT ''")


def downgrade() -> None:
    raise RuntimeError("Die Browser-Einreichung wird nicht automatisch entfernt; Backup/Restore ist erforderlich.")
