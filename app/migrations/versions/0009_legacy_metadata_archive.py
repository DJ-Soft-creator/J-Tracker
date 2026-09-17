"""Archive validated legacy metadata before clean Markdown materialization.

Revision ID: 0009_legacy_metadata_archive
Revises: 0008_agent_draft_commit
"""

from alembic import op


revision = "0009_legacy_metadata_archive"
down_revision = "0008_agent_draft_commit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    connection.exec_driver_sql("""
        CREATE TABLE legacy_metadata_archive (
            source_revision_id uuid NOT NULL REFERENCES document_revisions(id) ON DELETE RESTRICT,
            source_file_hash char(64) NOT NULL,
            marker_kind text NOT NULL,
            ordinal integer NOT NULL CHECK (ordinal >= 0),
            payload jsonb NOT NULL,
            extracted_at timestamptz NOT NULL,
            CONSTRAINT pk_legacy_metadata_archive PRIMARY KEY (source_revision_id, marker_kind, ordinal)
        )
    """)
    connection.exec_driver_sql("CREATE INDEX ix_legacy_metadata_archive_kind ON legacy_metadata_archive (marker_kind)")


def downgrade() -> None:
    raise RuntimeError("Archivierte Legacy-Metadaten werden nicht automatisch entfernt; Backup/Restore ist erforderlich.")
