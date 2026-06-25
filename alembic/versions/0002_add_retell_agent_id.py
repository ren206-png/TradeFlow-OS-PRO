"""add retell_agent_id to contractors

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-24

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("contractors", sa.Column("retell_agent_id", sa.String(128), nullable=True))
    op.create_index("ix_contractors_retell_agent_id", "contractors", ["retell_agent_id"])


def downgrade() -> None:
    op.drop_index("ix_contractors_retell_agent_id", "contractors")
    op.drop_column("contractors", "retell_agent_id")
