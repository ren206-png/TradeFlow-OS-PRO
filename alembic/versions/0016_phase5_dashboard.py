"""Phase 5: Owner Dashboard V2, Spam Shield & Push Notifications

Creates:
  - daily_call_stats table (unique constraint: tenant_id + stat_date)
  - spam_blocks table
  - push_subscriptions table
  - ADD COLUMN owner_dashboard_v2 BOOLEAN DEFAULT FALSE to contractors
  - ADD COLUMN spam_shield_enabled BOOLEAN DEFAULT FALSE to contractors

Revision ID: 0016
Revises: 0015
Create Date: 2026-08-19
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # daily_call_stats — nightly aggregation table; dashboard reads only this
    # ------------------------------------------------------------------
    op.create_table(
        "daily_call_stats",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("contractors.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("stat_date", sa.Date(), nullable=False),
        sa.Column("calls_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("calls_answered", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("calls_after_hours", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("calls_booked", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("calls_abandoned", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("calls_transferred", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("calls_spam_blocked", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("avg_duration_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("avg_time_to_answer_ms", sa.Integer(), nullable=False, server_default="0"),
        # Integer percentage 0-100 — never float
        sa.Column("booking_rate_pct", sa.Integer(), nullable=False, server_default="0"),
        # Integer cents — no floats
        sa.Column("estimated_revenue_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(3), nullable=False, server_default="CAD"),
        # always true — label is non-negotiable
        sa.Column("is_estimated", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("missed_calls_recovered", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reminders_sent", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("no_shows_prevented", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        # Unique constraint: one row per tenant per day
        sa.UniqueConstraint("tenant_id", "stat_date", name="uq_daily_call_stats_tenant_date"),
    )
    op.create_index("ix_daily_call_stats_tenant_id", "daily_call_stats", ["tenant_id"])
    op.create_index("ix_daily_call_stats_stat_date", "daily_call_stats", ["stat_date"])

    # ------------------------------------------------------------------
    # spam_blocks — reviewable blocks, one-tap unblockable
    # ------------------------------------------------------------------
    op.create_table(
        "spam_blocks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("contractors.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("phone_number", sa.String(30), nullable=False),
        # robocall_pattern|repeat_hangup|cross_tenant_abuse|carrier_spam|manual_block
        sa.Column("block_reason", sa.String(50), nullable=False),
        # behavioral|carrier|manual
        sa.Column("block_source", sa.String(50), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        # one-tap unblock stamps this
        sa.Column("false_positive_reported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_spam_blocks_tenant_id", "spam_blocks", ["tenant_id"])
    op.create_index("ix_spam_blocks_phone_number", "spam_blocks", ["phone_number"])

    # ------------------------------------------------------------------
    # push_subscriptions — VAPID web push subscriptions
    # ------------------------------------------------------------------
    op.create_table(
        "push_subscriptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("contractors.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("auth", sa.String(255), nullable=False),
        sa.Column("p256dh", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_push_subscriptions_tenant_id", "push_subscriptions", ["tenant_id"])

    # ------------------------------------------------------------------
    # ADD COLUMNS to contractors (additive only — no existing changes)
    # ------------------------------------------------------------------
    op.add_column(
        "contractors",
        sa.Column("owner_dashboard_v2", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "contractors",
        sa.Column("spam_shield_enabled", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("contractors", "spam_shield_enabled")
    op.drop_column("contractors", "owner_dashboard_v2")

    op.drop_index("ix_push_subscriptions_tenant_id", table_name="push_subscriptions")
    op.drop_table("push_subscriptions")

    op.drop_index("ix_spam_blocks_phone_number", table_name="spam_blocks")
    op.drop_index("ix_spam_blocks_tenant_id", table_name="spam_blocks")
    op.drop_table("spam_blocks")

    op.drop_index("ix_daily_call_stats_stat_date", table_name="daily_call_stats")
    op.drop_index("ix_daily_call_stats_tenant_id", table_name="daily_call_stats")
    op.drop_table("daily_call_stats")
