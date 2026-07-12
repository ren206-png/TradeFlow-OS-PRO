"""add fsm_credentials and fsm_retry_queue tables

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-11

"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from alembic import op

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "fsm_credentials",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("contractor_id", UUID(as_uuid=True), sa.ForeignKey("contractors.id"), nullable=False, unique=True),
        sa.Column("vendor", sa.String(50), nullable=False),
        sa.Column("access_token_enc", sa.String(500), nullable=False),
        sa.Column("refresh_token_enc", sa.String(500), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "fsm_retry_queue",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("contractor_id", UUID(as_uuid=True), sa.ForeignKey("contractors.id"), nullable=False),
        sa.Column("lead_id", UUID(as_uuid=True), sa.ForeignKey("leads.id"), nullable=True),
        sa.Column("vendor", sa.String(50), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
    )

    op.add_column(
        "contractors",
        sa.Column(
            "fsm_sync_enabled",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )


def downgrade() -> None:
    op.drop_table("fsm_retry_queue")
    op.drop_table("fsm_credentials")
    op.drop_column("contractors", "fsm_sync_enabled")
