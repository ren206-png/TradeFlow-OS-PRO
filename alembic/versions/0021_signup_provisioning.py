"""Email verification + provisioning state for new signups.

Revision ID: 0021
Revises: 0020
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0021"
down_revision: Union[str, None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE contractors ADD COLUMN IF NOT EXISTS email_verified_at TIMESTAMPTZ")
    op.execute("ALTER TABLE contractors ADD COLUMN IF NOT EXISTS provisioning_status VARCHAR(24) NOT NULL DEFAULT 'active'")
    op.execute("ALTER TABLE contractors ADD COLUMN IF NOT EXISTS provisioned_at TIMESTAMPTZ")
    op.execute("ALTER TABLE contractors ADD COLUMN IF NOT EXISTS provisioning_attempts INTEGER NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE contractors ADD COLUMN IF NOT EXISTS provisioning_error VARCHAR(255)")
    # Existing accounts are already live: treat them as verified and provisioned.
    op.execute("UPDATE contractors SET email_verified_at = now() WHERE email IS NOT NULL AND email_verified_at IS NULL")
    op.execute("UPDATE contractors SET provisioned_at = created_at WHERE provisioned_at IS NULL AND retell_agent_id IS NOT NULL")
    op.execute("CREATE INDEX IF NOT EXISTS ix_contractors_provisioning_status ON contractors (provisioning_status)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_contractors_provisioning_status")
    for col in ("provisioning_error", "provisioning_attempts", "provisioned_at", "provisioning_status", "email_verified_at"):
        op.execute(f"ALTER TABLE contractors DROP COLUMN IF EXISTS {col}")
