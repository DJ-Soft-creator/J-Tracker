"""Store verified attachment references for confirmed document revisions.

Revision ID: 0003_media_references
Revises: 0002_document_search
Create Date: 2026-08-12
"""

from alembic import op


revision = "0003_media_references"
down_revision = "0002_document_search"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    connection.exec_driver_sql("""
        CREATE TABLE document_media_references (
            document_revision_id uuid NOT NULL REFERENCES document_revisions(id) ON DELETE RESTRICT,
            media_id uuid NOT NULL,
            media_path text NOT NULL,
            media_type text NOT NULL CHECK (media_type IN ('image', 'audio', 'document')),
            mime_type text NOT NULL,
            file_hash char(64) NOT NULL,
            byte_size bigint NOT NULL CHECK (byte_size >= 0),
            verified_at timestamptz NOT NULL,
            CONSTRAINT pk_document_media_references PRIMARY KEY (document_revision_id, media_id)
        )
    """)
    connection.exec_driver_sql("CREATE INDEX ix_document_media_references_media_id ON document_media_references (media_id)")


def downgrade() -> None:
    raise RuntimeError("Die Medienreferenz-Migration wird nicht automatisch entfernt; Backup/Restore ist erforderlich.")
