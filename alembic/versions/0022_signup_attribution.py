"""Ad attribution captured at signup (utm_*, ttclid, landing referrer).

Revision ID: 0022
Revises: 0021
"""
from alembic import op
import sqlalchemy as sa

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("contractors", sa.Column("attribution", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("contractors", "attribution")
