"""Phase 1: Compliance Foundation & Outbound Gateway

Creates:
  - feature_flags table
  - outbound_ledger table
  - consent_ledger table
  - a2p_registration table
  - ADD COLUMN outbound_daily_cap to contractors
  - ADD COLUMN outbound_gateway_enabled to contractors

Also adds CREATE TABLE IF NOT EXISTS for sms_opt_outs and sms_consents at the
top of upgrade() to fix schema drift found in Phase 0 audit. These tables were
not in the Alembic chain and must be created separately when starting a fresh DB.

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-19
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # Phase 0 schema drift fix: sms_opt_outs & sms_consents were never
    # in the Alembic chain. Use IF NOT EXISTS so this is idempotent on
    # databases where these tables already exist.
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS sms_opt_outs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            phone VARCHAR(30) NOT NULL UNIQUE,
            opted_out_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            opted_back_in_at TIMESTAMPTZ,
            is_opted_out BOOLEAN NOT NULL DEFAULT TRUE
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_sms_opt_outs_phone ON sms_opt_outs (phone)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS sms_consents (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            phone VARCHAR(30) NOT NULL,
            source_call_id VARCHAR(128) NOT NULL,
            consent_basis VARCHAR(50) NOT NULL DEFAULT 'inbound_caller',
            consented_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            first_sms_sent BOOLEAN NOT NULL DEFAULT FALSE
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_sms_consents_phone ON sms_consents (phone)")

    # ------------------------------------------------------------------
    # feature_flags
    # ------------------------------------------------------------------
    op.create_table(
        "feature_flags",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("flag_key", sa.String(128), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_feature_flags_tenant_id", "feature_flags", ["tenant_id"])
    op.create_index("ix_feature_flags_flag_key", "feature_flags", ["flag_key"])

    # ------------------------------------------------------------------
    # outbound_ledger (append-only)
    # ------------------------------------------------------------------
    op.create_table(
        "outbound_ledger",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("recipient_phone", sa.String(30), nullable=False),
        sa.Column("channel", sa.String(10), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("block_reason", sa.String(255), nullable=True),
        sa.Column("template_id", sa.String(128), nullable=True),
        sa.Column("campaign_id", sa.String(128), nullable=True),
        sa.Column("message_preview", sa.String(160), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_outbound_ledger_tenant_id", "outbound_ledger", ["tenant_id"])
    op.create_index(
        "ix_outbound_ledger_idempotency_key", "outbound_ledger", ["idempotency_key"], unique=True
    )
    op.create_index(
        "ix_outbound_ledger_tenant_created",
        "outbound_ledger",
        ["tenant_id", "created_at"],
    )

    # ------------------------------------------------------------------
    # consent_ledger (append-only)
    # ------------------------------------------------------------------
    op.create_table(
        "consent_ledger",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("recipient_phone", sa.String(30), nullable=False),
        sa.Column("channel", sa.String(10), nullable=False),
        sa.Column("consent_type", sa.String(30), nullable=False),
        sa.Column("consent_basis", sa.String(255), nullable=False),
        sa.Column("evidence_call_id", sa.String(128), nullable=True),
        sa.Column("evidence_form_id", sa.String(128), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_consent_ledger_tenant_id", "consent_ledger", ["tenant_id"])
    op.create_index("ix_consent_ledger_recipient_phone", "consent_ledger", ["recipient_phone"])
    op.create_index(
        "ix_consent_ledger_tenant_phone_channel",
        "consent_ledger",
        ["tenant_id", "recipient_phone", "channel"],
    )

    # ------------------------------------------------------------------
    # a2p_registration
    # ------------------------------------------------------------------
    op.create_table(
        "a2p_registration",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("brand_registration_id", sa.String(128), nullable=True),
        sa.Column("campaign_id", sa.String(128), nullable=True),
        sa.Column("status", sa.String(30), nullable=False, server_default="unregistered"),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_reason", sa.String(512), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_a2p_registration_tenant_id", "a2p_registration", ["tenant_id"], unique=True)

    # ------------------------------------------------------------------
    # ADD COLUMN to contractors (additive only — no existing columns touched)
    # ------------------------------------------------------------------
    op.add_column(
        "contractors",
        sa.Column("outbound_daily_cap", sa.Integer(), nullable=True, server_default="500"),
    )
    op.add_column(
        "contractors",
        sa.Column(
            "outbound_gateway_enabled",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )


def downgrade() -> None:
    op.drop_column("contractors", "outbound_gateway_enabled")
    op.drop_column("contractors", "outbound_daily_cap")
    op.drop_index("ix_a2p_registration_tenant_id", table_name="a2p_registration")
    op.drop_table("a2p_registration")
    op.drop_index("ix_consent_ledger_tenant_phone_channel", table_name="consent_ledger")
    op.drop_index("ix_consent_ledger_recipient_phone", table_name="consent_ledger")
    op.drop_index("ix_consent_ledger_tenant_id", table_name="consent_ledger")
    op.drop_table("consent_ledger")
    op.drop_index("ix_outbound_ledger_tenant_created", table_name="outbound_ledger")
    op.drop_index("ix_outbound_ledger_idempotency_key", table_name="outbound_ledger")
    op.drop_index("ix_outbound_ledger_tenant_id", table_name="outbound_ledger")
    op.drop_table("outbound_ledger")
    op.drop_index("ix_feature_flags_flag_key", table_name="feature_flags")
    op.drop_index("ix_feature_flags_tenant_id", table_name="feature_flags")
    op.drop_table("feature_flags")
    # Note: sms_opt_outs and sms_consents are NOT dropped in downgrade —
    # they pre-date the Alembic chain and dropping them would be destructive.
