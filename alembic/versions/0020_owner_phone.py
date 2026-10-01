"""Add contractors.owner_phone (owner's mobile for alerts), separate from the AI line.

Revision ID: 0020
Revises: 0019
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0020"
down_revision: Union[str, None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE contractors ADD COLUMN IF NOT EXISTS owner_phone VARCHAR(30)")
    # Best existing signal for the owner's phone: the configured transfer number.
    op.execute(
        "UPDATE contractors SET owner_phone = calendar_config->>'transfer_number' "
        "WHERE owner_phone IS NULL AND coalesce(calendar_config->>'transfer_number', '') <> ''"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE contractors DROP COLUMN IF EXISTS owner_phone")
