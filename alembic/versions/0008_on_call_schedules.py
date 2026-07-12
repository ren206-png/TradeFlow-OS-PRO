"""add on_call_schedules table and live_transfer_enabled column

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-11

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "on_call_schedules",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("contractor_id", sa.Integer(), sa.ForeignKey("contractors.id"), nullable=False),
        sa.Column("day_of_week", sa.Integer(), nullable=False),
        sa.Column("start_time", sa.String(8), nullable=False),
        sa.Column("end_time", sa.String(8), nullable=False),
        sa.Column("phone_number", sa.String(30), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
    )

    op.add_column(
        "contractors",
        sa.Column(
            "live_transfer_enabled",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )


def downgrade() -> None:
    op.drop_column("contractors", "live_transfer_enabled")
    op.drop_table("on_call_schedules")
