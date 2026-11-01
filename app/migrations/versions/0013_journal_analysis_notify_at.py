"""Benachrichtigungen zeitlich steuern: notify_at fuer geplante Hinweise.

Revision ID: 0013_journal_analysis_notify_at
Revises: 0012_journal_analysis_outputs
Create Date: 2026-09-24

Additiv: ``notify_at`` auf ``analysis_notifications``. Die Analyse liefert pro
erkanntem Ereignis einen absoluten Anzeigezeitpunkt (relativ zum Journaltag);
bis dahin bleibt die Meldung unsichtbar/„geplant". ``NULL`` bedeutet: sofort
anzeigen (bisheriges Verhalten).
"""

from alembic import op


revision = "0013_journal_analysis_notify_at"
down_revision = "0012_journal_analysis_outputs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    connection.exec_driver_sql(
        "ALTER TABLE analysis_notifications ADD COLUMN notify_at timestamptz NULL")
    connection.exec_driver_sql(
        "ALTER TABLE analysis_notifications DROP CONSTRAINT analysis_notifications_kind_check")
    connection.exec_driver_sql(
        "ALTER TABLE analysis_notifications ADD CONSTRAINT analysis_notifications_kind_check "
        "CHECK (kind IN ('analysis', 'summary', 'error', 'reminder'))")
    connection.exec_driver_sql(
        "CREATE INDEX ix_analysis_notifications_due "
        "ON analysis_notifications (owner_id, status, notify_at)")


def downgrade() -> None:
    connection = op.get_bind()
    connection.exec_driver_sql("DROP INDEX IF EXISTS ix_analysis_notifications_due")
    connection.exec_driver_sql(
        "ALTER TABLE analysis_notifications DROP CONSTRAINT analysis_notifications_kind_check")
    connection.exec_driver_sql(
        "ALTER TABLE analysis_notifications ADD CONSTRAINT analysis_notifications_kind_check "
        "CHECK (kind IN ('analysis', 'summary', 'error'))")
    connection.exec_driver_sql("ALTER TABLE analysis_notifications DROP COLUMN notify_at")