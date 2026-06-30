"""add contractor auth columns

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-30

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("contractors", sa.Column("email", sa.String(), nullable=True))
    op.add_column("contractors", sa.Column("hashed_password", sa.String(), nullable=True))
    op.add_column(
        "contractors",
        sa.Column("is_verified", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.create_unique_constraint("uq_contractors_email", "contractors", ["email"])


def downgrade() -> None:
    op.drop_constraint("uq_contractors_email", "contractors", type_="unique")
    op.drop_column("contractors", "is_verified")
    op.drop_column("contractors", "hashed_password")
    op.drop_column("contractors", "email")
