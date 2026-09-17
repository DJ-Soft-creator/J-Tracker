"""Create the deliberately minimal Phase-0B migration probe.

Revision ID: 0001_spike_probe
Revises:
Create Date: 2026-08-11
"""

from alembic import op
import sqlalchemy as sa


revision = "0001_spike_probe"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "db_spike_probe",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("note", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("db_spike_probe")
