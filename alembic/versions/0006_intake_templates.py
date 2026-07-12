"""add intake_templates table

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-11

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "intake_templates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("contractor_id", sa.Integer(), nullable=True),
        sa.Column("trade", sa.String(100), nullable=False),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("questions", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["contractor_id"], ["contractors.id"]),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("intake_templates")
