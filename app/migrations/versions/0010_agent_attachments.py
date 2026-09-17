"""Store immutable image snapshots for AI requests.

Revision ID: 0010_agent_attachments
Revises: 0009_legacy_metadata_archive
"""

from alembic import op


revision = "0010_agent_attachments"
down_revision = "0009_legacy_metadata_archive"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE agent_requests ADD COLUMN attachment_images jsonb NOT NULL DEFAULT '[]'::jsonb")


def downgrade() -> None:
    op.execute("ALTER TABLE agent_requests DROP COLUMN attachment_images")
