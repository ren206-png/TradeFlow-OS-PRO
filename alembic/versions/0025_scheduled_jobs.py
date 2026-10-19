"""Persist one-off delayed jobs so follow-up texts and reminders survive a restart.

Revision ID: 0025
Revises: 0024
"""
from alembic import op
import sqlalchemy as sa

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scheduled_jobs",
        sa.Column("id", sa.String(160), primary_key=True),
        sa.Column("func", sa.String(64), nullable=False),
        sa.Column("run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kwargs", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_scheduled_jobs_status", "scheduled_jobs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_scheduled_jobs_status", table_name="scheduled_jobs")
    op.drop_table("scheduled_jobs")
