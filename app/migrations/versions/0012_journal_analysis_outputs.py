"""Erweitert Analyse-Jobs um Zusammenfassungs-, Uebergabe- und Notizziele.

Revision ID: 0012_journal_analysis_outputs
Revises: 0011_journal_analysis
Create Date: 2026-09-24

Additiv: neue Spalten auf ``analysis_jobs``. ``handoff_summary`` wird vom Lauf
ueberschrieben (nie angehaengt), damit die Uebergabe nicht unbegrenzt waechst.
"""

from alembic import op


revision = "0012_journal_analysis_outputs"
down_revision = "0011_journal_analysis"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    connection.exec_driver_sql(
        "ALTER TABLE analysis_jobs ADD COLUMN output_run_summary boolean NOT NULL DEFAULT true")
    connection.exec_driver_sql(
        "ALTER TABLE analysis_jobs ADD COLUMN carry_summary boolean NOT NULL DEFAULT false")
    connection.exec_driver_sql(
        "ALTER TABLE analysis_jobs ADD COLUMN handoff_summary text NOT NULL DEFAULT ''")
    connection.exec_driver_sql(
        "ALTER TABLE analysis_jobs ADD COLUMN output_notes boolean NOT NULL DEFAULT false")
    connection.exec_driver_sql(
        "ALTER TABLE analysis_jobs ADD COLUMN notes_target text NULL")


def downgrade() -> None:
    connection = op.get_bind()
    for column in ("notes_target", "output_notes", "handoff_summary",
                   "carry_summary", "output_run_summary"):
        connection.exec_driver_sql(f"ALTER TABLE analysis_jobs DROP COLUMN {column}")