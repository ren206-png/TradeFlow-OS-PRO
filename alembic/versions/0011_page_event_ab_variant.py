"""add ab_variant to page_events

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-12

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "page_events",
        sa.Column("ab_variant", sa.String(2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("page_events", "ab_variant")
