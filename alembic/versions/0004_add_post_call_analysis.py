"""add post-call analysis columns to leads

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-29

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("leads", sa.Column("ai_summary", sa.Text(), nullable=True))
    op.add_column("leads", sa.Column("sentiment", sa.String(20), nullable=True))
    op.add_column(
        "leads",
        sa.Column("follow_up_recommended", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "leads",
        sa.Column("review_requested", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("leads", "review_requested")
    op.drop_column("leads", "follow_up_recommended")
    op.drop_column("leads", "sentiment")
    op.drop_column("leads", "ai_summary")
