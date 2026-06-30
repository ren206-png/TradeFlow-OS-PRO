"""add billing columns to contractors

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-29

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("contractors", sa.Column("stripe_customer_id", sa.String(128), nullable=True))
    op.add_column("contractors", sa.Column("stripe_subscription_id", sa.String(128), nullable=True))
    op.add_column(
        "contractors",
        sa.Column("subscription_status", sa.String(30), nullable=False, server_default="trial"),
    )
    op.add_column(
        "contractors",
        sa.Column("trial_ends_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "contractors",
        sa.Column("calls_this_month", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "contractors",
        sa.Column("sms_this_month", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "contractors",
        sa.Column("plan", sa.String(30), nullable=False, server_default="starter"),
    )


def downgrade() -> None:
    op.drop_column("contractors", "plan")
    op.drop_column("contractors", "sms_this_month")
    op.drop_column("contractors", "calls_this_month")
    op.drop_column("contractors", "trial_ends_at")
    op.drop_column("contractors", "subscription_status")
    op.drop_column("contractors", "stripe_subscription_id")
    op.drop_column("contractors", "stripe_customer_id")
