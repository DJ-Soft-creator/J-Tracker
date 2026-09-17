"""Create the initial Journl document-history schema.

Revision ID: 0001_db_base
Revises:
Create Date: 2026-08-11
"""

from pathlib import Path

from alembic import op


revision = "0001_db_base"
down_revision = None
branch_labels = None
depends_on = None


def _statements():
    schema = Path(__file__).resolve().parents[1] / "schema.sql"
    ddl = "\n".join(
        line for line in schema.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("--")
    )
    return [statement.strip() for statement in ddl.split(";") if statement.strip()]


def upgrade() -> None:
    connection = op.get_bind()
    for statement in _statements():
        connection.exec_driver_sql(statement)


def downgrade() -> None:
    raise RuntimeError("Das initiale Revisionsschema wird nicht automatisch geloescht; Backup/Restore ist erforderlich.")
