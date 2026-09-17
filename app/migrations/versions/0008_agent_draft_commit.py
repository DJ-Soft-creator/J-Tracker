"""Remember one DB-agent result applied to the private writing draft.

Revision ID: 0008_agent_draft_commit
Revises: 0007_agent_submitted_text
"""

from alembic import op


revision = "0008_agent_draft_commit"
down_revision = "0007_agent_submitted_text"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE agent_requests ADD COLUMN draft_commit_revision text NULL")


def downgrade() -> None:
    raise RuntimeError("Die Entwurfsuebernahme wird nicht automatisch entfernt; Backup/Restore ist erforderlich.")
