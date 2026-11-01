"""Benutzerdefinierter Zeitplan fuer Analyse-Jobs (Wochentage / Monatstage).

Revision ID: 0014_analysis_custom_schedule
Revises: 0013_journal_analysis_notify_at
Create Date: 2026-10-02

Additiv: ``schedule_rule`` (jsonb) auf ``analysis_jobs`` und der neue Modus
``custom``. Die Regel ist ``{"kind": "weekly", "weekdays": [4]}`` (0 = Montag)
oder ``{"kind": "monthly", "days": [1]}``; die Uhrzeit kommt weiter aus
``schedule_time``. ``NULL`` bei allen anderen Modi.
"""

from alembic import op


revision = "0014_analysis_custom_schedule"
down_revision = "0013_journal_analysis_notify_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    connection.exec_driver_sql(
        "ALTER TABLE analysis_jobs ADD COLUMN schedule_rule jsonb NULL")
    connection.exec_driver_sql(
        "ALTER TABLE analysis_jobs DROP CONSTRAINT analysis_jobs_schedule_mode_check")
    connection.exec_driver_sql(
        "ALTER TABLE analysis_jobs ADD CONSTRAINT analysis_jobs_schedule_mode_check "
        "CHECK (schedule_mode IN ('daily', 'interval', 'manual', 'custom'))")


def downgrade() -> None:
    connection = op.get_bind()
    connection.exec_driver_sql(
        "UPDATE analysis_jobs SET schedule_mode = 'manual' WHERE schedule_mode = 'custom'")
    connection.exec_driver_sql(
        "ALTER TABLE analysis_jobs DROP CONSTRAINT analysis_jobs_schedule_mode_check")
    connection.exec_driver_sql(
        "ALTER TABLE analysis_jobs ADD CONSTRAINT analysis_jobs_schedule_mode_check "
        "CHECK (schedule_mode IN ('daily', 'interval', 'manual'))")
    connection.exec_driver_sql("ALTER TABLE analysis_jobs DROP COLUMN schedule_rule")
