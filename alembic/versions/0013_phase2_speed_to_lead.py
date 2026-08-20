"""Phase 2: Speed-to-Lead & Missed-Call Recovery

Creates:
  - callback_requests table
  - ADD COLUMN lead_received_at TIMESTAMP to leads
  - ADD COLUMN first_contact_attempted_at TIMESTAMP to leads
  - ADD COLUMN speed_to_lead_seconds INTEGER to leads
  - ADD COLUMN webhook_secret VARCHAR(128) to contractors
  - ADD COLUMN booking_url VARCHAR(512) to contractors

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-19
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # callback_requests table
    # ------------------------------------------------------------------
    op.create_table(
        "callback_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("caller_phone", sa.String(30), nullable=False),
        sa.Column("form_submission_id", sa.String(255), nullable=True),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("issue", sa.Text(), nullable=True),
        sa.Column("source", sa.String(128), nullable=False, server_default="webhook"),
        sa.Column("status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outbound_call_id", sa.String(128), nullable=True),
        sa.Column(
            "lead_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("leads.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
    )
    op.create_index("ix_callback_requests_tenant_id", "callback_requests", ["tenant_id"])
    op.create_index("ix_callback_requests_caller_phone", "callback_requests", ["caller_phone"])
    op.create_index(
        "ix_callback_requests_form_submission_id",
        "callback_requests",
        ["form_submission_id"],
        unique=True,
    )
    op.create_index(
        "ix_callback_requests_tenant_phone",
        "callback_requests",
        ["tenant_id", "caller_phone"],
    )

    # ------------------------------------------------------------------
    # ADD COLUMN to leads (additive only)
    # ------------------------------------------------------------------
    op.add_column(
        "leads",
        sa.Column("lead_received_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "leads",
        sa.Column("first_contact_attempted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "leads",
        sa.Column("speed_to_lead_seconds", sa.Integer(), nullable=True),
    )

    # ------------------------------------------------------------------
    # ADD COLUMN to contractors (additive only)
    # ------------------------------------------------------------------
    op.add_column(
        "contractors",
        sa.Column("webhook_secret", sa.String(128), nullable=True),
    )
    op.add_column(
        "contractors",
        sa.Column("booking_url", sa.String(512), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("contractors", "booking_url")
    op.drop_column("contractors", "webhook_secret")
    op.drop_column("leads", "speed_to_lead_seconds")
    op.drop_column("leads", "first_contact_attempted_at")
    op.drop_column("leads", "lead_received_at")
    op.drop_index("ix_callback_requests_tenant_phone", table_name="callback_requests")
    op.drop_index("ix_callback_requests_form_submission_id", table_name="callback_requests")
    op.drop_index("ix_callback_requests_caller_phone", table_name="callback_requests")
    op.drop_index("ix_callback_requests_tenant_id", table_name="callback_requests")
    op.drop_table("callback_requests")
