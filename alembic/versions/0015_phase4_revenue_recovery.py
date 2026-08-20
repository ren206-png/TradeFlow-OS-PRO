"""Phase 4: Revenue Recovery Campaigns & Appointment Lifecycle

Creates:
  - appointments table
  - estimates table
  - revenue_attribution_ledger table (append-only)
  - campaigns table
  - campaign_contacts table
  - ADD COLUMN outbound_paused BOOLEAN DEFAULT FALSE to contractors
  - ADD COLUMN outbound_paused_at TIMESTAMP to contractors
  - ADD COLUMN avg_ticket_cents INTEGER to contractors
  - ADD COLUMN avg_ticket_cents_by_trade JSONB to contractors

Revision ID: 0015
Revises: 0014
Create Date: 2026-08-19
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # appointments
    # ------------------------------------------------------------------
    op.create_table(
        "appointments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("contractors.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "lead_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("leads.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("caller_phone", sa.String(30), nullable=False),
        sa.Column("caller_name", sa.String(255), nullable=True),
        sa.Column("appointment_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("job_type", sa.String(128), nullable=True),
        sa.Column("status", sa.String(30), nullable=False, server_default="scheduled"),
        sa.Column("fsm_appointment_id", sa.String(255), nullable=True),
        sa.Column("confirmation_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reminder_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reschedule_offered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_appointments_tenant_id", "appointments", ["tenant_id"])
    op.create_index("ix_appointments_lead_id", "appointments", ["lead_id"])
    op.create_index("ix_appointments_caller_phone", "appointments", ["caller_phone"])

    # ------------------------------------------------------------------
    # estimates
    # ------------------------------------------------------------------
    op.create_table(
        "estimates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("contractors.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "lead_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("leads.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("caller_phone", sa.String(30), nullable=False),
        sa.Column("caller_name", sa.String(255), nullable=True),
        # Integer cents only — no floats
        sa.Column("estimate_value_cents", sa.Integer(), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False, server_default="CAD"),
        sa.Column("status", sa.String(30), nullable=False, server_default="draft"),
        sa.Column("fsm_estimate_id", sa.String(255), nullable=True),
        sa.Column("source", sa.String(50), nullable=False, server_default="manual"),
        sa.Column("followup_enrolled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("followup_step", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("followup_paused", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_estimates_tenant_id", "estimates", ["tenant_id"])
    op.create_index("ix_estimates_lead_id", "estimates", ["lead_id"])
    op.create_index("ix_estimates_caller_phone", "estimates", ["caller_phone"])

    # ------------------------------------------------------------------
    # revenue_attribution_ledger — append-only; NO UPDATE, NO DELETE
    # ------------------------------------------------------------------
    op.create_table(
        "revenue_attribution_ledger",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("contractors.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column(
            "lead_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("leads.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "estimate_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("estimates.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "appointment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("appointments.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("campaign_id", sa.String(128), nullable=True),
        sa.Column("campaign_step", sa.Integer(), nullable=True),
        # Integer cents only — no floats ever
        sa.Column("attributed_value_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        # True unless verified via FSM
        sa.Column("is_estimated", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        # Correction rows reference original_id
        sa.Column("original_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_correction", sa.Boolean(), nullable=False, server_default="false"),
        # NO updated_at column — append-only ledger
    )
    op.create_index("ix_revenue_attribution_ledger_tenant_id", "revenue_attribution_ledger", ["tenant_id"])
    op.create_index("ix_revenue_attribution_ledger_estimate_id", "revenue_attribution_ledger", ["estimate_id"])
    op.create_index("ix_revenue_attribution_ledger_appointment_id", "revenue_attribution_ledger", ["appointment_id"])

    # ------------------------------------------------------------------
    # campaigns
    # ------------------------------------------------------------------
    op.create_table(
        "campaigns",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("contractors.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("campaign_type", sa.String(50), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="active"),
        sa.Column("trade", sa.String(50), nullable=True),
        sa.Column("season", sa.String(20), nullable=True),
        sa.Column("daily_send_cap", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("total_sent", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_converted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_campaigns_tenant_id", "campaigns", ["tenant_id"])

    # ------------------------------------------------------------------
    # campaign_contacts
    # ------------------------------------------------------------------
    op.create_table(
        "campaign_contacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "campaign_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("campaigns.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("contractors.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("recipient_phone", sa.String(30), nullable=False),
        sa.Column("recipient_name", sa.String(255), nullable=True),
        sa.Column("status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("current_step", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_contacted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("converted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_campaign_contacts_campaign_id", "campaign_contacts", ["campaign_id"])
    op.create_index("ix_campaign_contacts_tenant_id", "campaign_contacts", ["tenant_id"])
    op.create_index("ix_campaign_contacts_recipient_phone", "campaign_contacts", ["recipient_phone"])

    # ------------------------------------------------------------------
    # ADD COLUMNS to contractors (additive only)
    # ------------------------------------------------------------------
    op.add_column(
        "contractors",
        sa.Column("outbound_paused", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "contractors",
        sa.Column("outbound_paused_at", sa.DateTime(timezone=True), nullable=True),
    )
    # avg_ticket_cents: integer cents per trade for revenue attribution fallback
    op.add_column(
        "contractors",
        sa.Column("avg_ticket_cents", sa.Integer(), nullable=True),
    )
    # avg_ticket_cents_by_trade: {"plumbing": 25000, "hvac": 35000} (integer cents per trade)
    op.add_column(
        "contractors",
        sa.Column("avg_ticket_cents_by_trade", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("contractors", "avg_ticket_cents_by_trade")
    op.drop_column("contractors", "avg_ticket_cents")
    op.drop_column("contractors", "outbound_paused_at")
    op.drop_column("contractors", "outbound_paused")
    op.drop_index("ix_campaign_contacts_recipient_phone", table_name="campaign_contacts")
    op.drop_index("ix_campaign_contacts_tenant_id", table_name="campaign_contacts")
    op.drop_index("ix_campaign_contacts_campaign_id", table_name="campaign_contacts")
    op.drop_table("campaign_contacts")
    op.drop_index("ix_campaigns_tenant_id", table_name="campaigns")
    op.drop_table("campaigns")
    op.drop_index("ix_revenue_attribution_ledger_appointment_id", table_name="revenue_attribution_ledger")
    op.drop_index("ix_revenue_attribution_ledger_estimate_id", table_name="revenue_attribution_ledger")
    op.drop_index("ix_revenue_attribution_ledger_tenant_id", table_name="revenue_attribution_ledger")
    op.drop_table("revenue_attribution_ledger")
    op.drop_index("ix_estimates_caller_phone", table_name="estimates")
    op.drop_index("ix_estimates_lead_id", table_name="estimates")
    op.drop_index("ix_estimates_tenant_id", table_name="estimates")
    op.drop_table("estimates")
    op.drop_index("ix_appointments_caller_phone", table_name="appointments")
    op.drop_index("ix_appointments_lead_id", table_name="appointments")
    op.drop_index("ix_appointments_tenant_id", table_name="appointments")
    op.drop_table("appointments")
