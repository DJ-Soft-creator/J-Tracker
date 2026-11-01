"""Zeitraum „Letzte N Tage“ fuer Analyse-Jobs.

Revision ID: 0015_analysis_last_days
Revises: 0014_analysis_custom_schedule
Create Date: 2026-10-04

Additiv: ``range_days`` (integer) auf ``analysis_jobs`` und der neue Modus
``last_days``. Der Zeitraum sind die letzten ``range_days`` Tage bis
einschließlich Laufdatum; ``NULL`` bei allen anderen Modi.
"""

from alembic import op


revision = "0015_analysis_last_days"
down_revision = "0014_analysis_custom_schedule"
branch_labels = None
depends_on = None

_MODES = "'previous_day', 'current_week', 'last_week', 'current_month', 'last_month', 'custom'"


def upgrade() -> None:
    connection = op.get_bind()
    connection.exec_driver_sql(
        "ALTER TABLE analysis_jobs ADD COLUMN range_days integer NULL "
        "CHECK (range_days IS NULL OR (range_days >= 1 AND range_days <= 62))")
    connection.exec_driver_sql(
        "ALTER TABLE analysis_jobs DROP CONSTRAINT analysis_jobs_date_range_mode_check")
    connection.exec_driver_sql(
        "ALTER TABLE analysis_jobs ADD CONSTRAINT analysis_jobs_date_range_mode_check "
        f"CHECK (date_range_mode IN ({_MODES}, 'last_days'))")
    connection.exec_driver_sql(
        "ALTER TABLE analysis_jobs ADD CONSTRAINT ck_analysis_jobs_last_days CHECK "
        "(date_range_mode <> 'last_days' OR range_days IS NOT NULL)")


def downgrade() -> None:
    connection = op.get_bind()
    connection.exec_driver_sql(
        "ALTER TABLE analysis_jobs DROP CONSTRAINT ck_analysis_jobs_last_days")
    connection.exec_driver_sql(
        "UPDATE analysis_jobs SET date_range_mode = 'previous_day' WHERE date_range_mode = 'last_days'")
    connection.exec_driver_sql(
        "ALTER TABLE analysis_jobs DROP CONSTRAINT analysis_jobs_date_range_mode_check")
    connection.exec_driver_sql(
        "ALTER TABLE analysis_jobs ADD CONSTRAINT analysis_jobs_date_range_mode_check "
        f"CHECK (date_range_mode IN ({_MODES}))")
    connection.exec_driver_sql("ALTER TABLE analysis_jobs DROP COLUMN range_days")
