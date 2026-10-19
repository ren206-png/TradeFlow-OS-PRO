"""How the owner wants the AI to answer: only missed calls (default) or every call.

Revision ID: 0024
Revises: 0023
"""
from alembic import op
import sqlalchemy as sa

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("contractors", sa.Column("call_handling", sa.String(12), nullable=False, server_default="missed"))


def downgrade() -> None:
    op.drop_column("contractors", "call_handling")
