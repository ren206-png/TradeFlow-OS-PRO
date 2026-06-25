"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-06-24

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "contractors",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("agent_name", sa.String(100), nullable=False),
        sa.Column("phone_number", sa.String(30), nullable=False, unique=True),
        sa.Column("api_key", sa.String(128), nullable=False, unique=True),
        sa.Column("trades", postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("service_areas", postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="America/New_York"),
        sa.Column("diagnostic_fee", sa.Float(), nullable=True),
        sa.Column("free_estimate", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("calendar_provider", sa.String(50), nullable=False, server_default="manual"),
        sa.Column("calendar_config", postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("sms_enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("review_link", sa.String(512), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_contractors_api_key", "contractors", ["api_key"])

    op.create_table(
        "leads",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("contractor_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("contractors.id", ondelete="CASCADE"), nullable=False),
        sa.Column("call_id", sa.String(128), nullable=False),
        sa.Column("caller_name", sa.String(255), nullable=True),
        sa.Column("phone", sa.String(30), nullable=True),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("service_address", sa.String(512), nullable=True),
        sa.Column("city", sa.String(100), nullable=True),
        sa.Column("province_state", sa.String(100), nullable=True),
        sa.Column("postal_zip", sa.String(20), nullable=True),
        sa.Column("property_type", sa.String(50), nullable=True),
        sa.Column("business_name", sa.String(255), nullable=True),
        sa.Column("trade", sa.String(100), nullable=True),
        sa.Column("service_category", sa.String(100), nullable=True),
        sa.Column("problem_summary", sa.Text(), nullable=True),
        sa.Column("emergency_level", sa.String(50), nullable=True),
        sa.Column("life_safety_risk", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("service_area_status", sa.String(20), nullable=False, server_default="unknown"),
        sa.Column("appointment_status", sa.String(30), nullable=False, server_default="not_booked"),
        sa.Column("appointment_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("calendar_event_id", sa.String(255), nullable=True),
        sa.Column("sms_confirmation_sent", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("human_transfer_required", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("transfer_reason", sa.String(100), nullable=True),
        sa.Column("emergency_score", sa.Integer(), nullable=True),
        sa.Column("revenue_score", sa.Integer(), nullable=True),
        sa.Column("close_probability", sa.Integer(), nullable=True),
        sa.Column("priority_level", sa.String(20), nullable=True),
        sa.Column("customer_sentiment", sa.String(50), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("call_direction", sa.String(30), nullable=False, server_default="inbound"),
        sa.Column("lead_source", sa.String(100), nullable=False, server_default="retell_call"),
        sa.Column("recording_url", sa.String(1024), nullable=True),
        sa.Column("transcript_url", sa.String(1024), nullable=True),
        sa.Column("raw_transcript", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_leads_contractor_id", "leads", ["contractor_id"])
    op.create_index("ix_leads_call_id", "leads", ["call_id"])

    op.create_table(
        "call_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("retell_call_id", sa.String(128), nullable=False, unique=True),
        sa.Column("contractor_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("contractors.id", ondelete="CASCADE"), nullable=False),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("leads.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("conversation_history", postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
    )
    op.create_index("ix_call_sessions_retell_call_id", "call_sessions", ["retell_call_id"])
    op.create_index("ix_call_sessions_contractor_id", "call_sessions", ["contractor_id"])


def downgrade() -> None:
    op.drop_table("call_sessions")
    op.drop_table("leads")
    op.drop_table("contractors")
