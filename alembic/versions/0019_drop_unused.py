"""Drop unused push_subscriptions table and contractors.spam_shield_enabled / owner_dashboard_v2 columns.

Revision ID: 0019
Revises: 0018
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0019"
down_revision: Union[str, None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS push_subscriptions")
    op.execute("ALTER TABLE contractors DROP COLUMN IF EXISTS spam_shield_enabled")
    op.execute("ALTER TABLE contractors DROP COLUMN IF EXISTS owner_dashboard_v2")


def downgrade() -> None:
    op.execute(
        "ALTER TABLE contractors ADD COLUMN IF NOT EXISTS spam_shield_enabled BOOLEAN NOT NULL DEFAULT FALSE"
    )
    op.execute(
        "ALTER TABLE contractors ADD COLUMN IF NOT EXISTS owner_dashboard_v2 BOOLEAN NOT NULL DEFAULT FALSE"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS push_subscriptions (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL REFERENCES contractors(id) ON DELETE CASCADE,
            endpoint TEXT NOT NULL,
            auth VARCHAR(255),
            p256dh VARCHAR(255),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
