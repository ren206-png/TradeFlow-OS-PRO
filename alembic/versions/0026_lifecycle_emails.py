"""Track which lifecycle emails each account has received.

Revision ID: 0026
Revises: 0025
"""
from alembic import op
import sqlalchemy as sa

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("contractors", sa.Column("lifecycle_sent", sa.String(255), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("contractors", "lifecycle_sent")
