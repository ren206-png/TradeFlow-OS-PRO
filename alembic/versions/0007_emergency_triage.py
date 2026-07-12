"""add emergency triage columns to contractors

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-11

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "contractors",
        sa.Column(
            "emergency_triage_enabled",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )
    op.add_column(
        "contractors",
        sa.Column("emergency_config", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("contractors", "emergency_config")
    op.drop_column("contractors", "emergency_triage_enabled")
