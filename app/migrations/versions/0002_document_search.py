"""Add confirmed-document full-text search index.

Revision ID: 0002_document_search
Revises: 0001_db_base
Create Date: 2026-08-12
"""

from alembic import op


revision = "0002_document_search"
down_revision = "0001_db_base"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    connection.exec_driver_sql("""
        CREATE TABLE document_search (
            document_id uuid PRIMARY KEY REFERENCES documents(id) ON DELETE RESTRICT,
            owner_id text NOT NULL,
            revision_id uuid NOT NULL REFERENCES document_revisions(id) ON DELETE RESTRICT,
            search_text text NOT NULL,
            search_vector tsvector NOT NULL,
            indexed_at timestamptz NOT NULL
        )
    """)
    connection.exec_driver_sql("CREATE INDEX ix_document_search_owner ON document_search (owner_id)")
    connection.exec_driver_sql("CREATE INDEX ix_document_search_vector ON document_search USING GIN (search_vector)")


def downgrade() -> None:
    raise RuntimeError("Die Suchindex-Migration wird nicht automatisch entfernt; Backup/Restore ist erforderlich.")
