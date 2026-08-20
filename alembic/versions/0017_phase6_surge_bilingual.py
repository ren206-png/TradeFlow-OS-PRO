"""Phase 6: Surge Intelligence & Canada-First Differentiation

Creates:
  - weather_alerts table
  - surge_mode_records table
  - contact_language_preferences table
  - service_agreements table
  - commercial_intake table
  ADD to contractors:
    - surge_mode_active BOOLEAN DEFAULT FALSE
    - surge_overbooking_multiplier NUMERIC(3,1) DEFAULT 1.0
    - service_area_postal_codes JSON
    - default_language VARCHAR(8) DEFAULT 'en'
    - french_voice_id VARCHAR(255)
    - tenant_type VARCHAR(50) DEFAULT 'residential'
    - primary_trade VARCHAR(50)

Revision ID: 0017
Revises: 0016
Create Date: 2026-08-20
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # weather_alerts
    # ------------------------------------------------------------------
    op.create_table(
        "weather_alerts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("contractors.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("alert_id", sa.String(255), nullable=False, unique=True),
        sa.Column("surge_type", sa.String(30), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("postal_codes", sa.JSON(), nullable=True),
        sa.Column("raw_payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_weather_alerts_tenant_id", "weather_alerts", ["tenant_id"])
    op.create_index("ix_weather_alerts_alert_id", "weather_alerts", ["alert_id"])

    # ------------------------------------------------------------------
    # surge_mode_records
    # ------------------------------------------------------------------
    op.create_table(
        "surge_mode_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("contractors.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "alert_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("weather_alerts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("surge_type", sa.String(30), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_manual", sa.Boolean(), nullable=False, server_default="false"),
        # Numeric(3,1) — max 1.5. Compare as int*10 to avoid floats.
        sa.Column("overbooking_multiplier", sa.Numeric(3, 1), nullable=False, server_default="1.0"),
        sa.Column("activated_by_alert_title", sa.String(512), nullable=True),
    )
    op.create_index("ix_surge_mode_records_tenant_id", "surge_mode_records", ["tenant_id"])
    op.create_index("ix_surge_mode_records_expires_at", "surge_mode_records", ["expires_at"])

    # ------------------------------------------------------------------
    # contact_language_preferences
    # ------------------------------------------------------------------
    op.create_table(
        "contact_language_preferences",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("contractors.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("phone_number", sa.String(30), nullable=False),
        sa.Column("language", sa.String(8), nullable=False),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "tenant_id", "phone_number", name="uq_contact_lang_pref_tenant_phone"
        ),
    )
    op.create_index(
        "ix_contact_language_preferences_phone_number",
        "contact_language_preferences",
        ["phone_number"],
    )
    op.create_index(
        "ix_contact_language_preferences_tenant_id",
        "contact_language_preferences",
        ["tenant_id"],
    )

    # ------------------------------------------------------------------
    # service_agreements
    # ------------------------------------------------------------------
    op.create_table(
        "service_agreements",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("contractors.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("company_name", sa.String(512), nullable=False),
        sa.Column("agreement_number", sa.String(128), nullable=False),
        sa.Column("plan_type", sa.String(128), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("priority_routing", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_service_agreements_tenant_id", "service_agreements", ["tenant_id"])
    op.create_index("ix_service_agreements_company_name", "service_agreements", ["company_name"])

    # ------------------------------------------------------------------
    # commercial_intake
    # ------------------------------------------------------------------
    op.create_table(
        "commercial_intake",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "lead_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("leads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("contractors.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("site_contact_name", sa.String(255), nullable=True),
        sa.Column("site_contact_phone", sa.String(30), nullable=True),
        sa.Column("account_contact_name", sa.String(255), nullable=True),
        sa.Column("po_number", sa.String(128), nullable=True),
        sa.Column("building_id", sa.String(128), nullable=True),
        sa.Column("unit_id", sa.String(128), nullable=True),
        sa.Column("equipment_tag_id", sa.String(128), nullable=True),
        sa.Column(
            "service_agreement_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("service_agreements.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("match_confidence", sa.Integer(), nullable=True),
        sa.Column("pipefield_payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_commercial_intake_tenant_id", "commercial_intake", ["tenant_id"])
    op.create_index("ix_commercial_intake_lead_id", "commercial_intake", ["lead_id"])

    # ------------------------------------------------------------------
    # ADD COLUMNS to contractors (additive only)
    # ------------------------------------------------------------------
    op.add_column(
        "contractors",
        sa.Column("surge_mode_active", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "contractors",
        sa.Column("surge_overbooking_multiplier", sa.Numeric(3, 1), nullable=False, server_default="1.0"),
    )
    op.add_column(
        "contractors",
        sa.Column("service_area_postal_codes", sa.JSON(), nullable=True),
    )
    op.add_column(
        "contractors",
        sa.Column("default_language", sa.String(8), nullable=False, server_default="en"),
    )
    op.add_column(
        "contractors",
        sa.Column("french_voice_id", sa.String(255), nullable=True),
    )
    op.add_column(
        "contractors",
        sa.Column("tenant_type", sa.String(50), nullable=False, server_default="residential"),
    )
    # primary_trade — add only if not already present (phase 3 may have added via getattr)
    op.add_column(
        "contractors",
        sa.Column("primary_trade", sa.String(50), nullable=True),
    )


def downgrade() -> None:
    # Remove contractor columns (reverse order)
    op.drop_column("contractors", "primary_trade")
    op.drop_column("contractors", "tenant_type")
    op.drop_column("contractors", "french_voice_id")
    op.drop_column("contractors", "default_language")
    op.drop_column("contractors", "service_area_postal_codes")
    op.drop_column("contractors", "surge_overbooking_multiplier")
    op.drop_column("contractors", "surge_mode_active")

    # Drop tables (reverse dependency order)
    op.drop_index("ix_commercial_intake_lead_id", table_name="commercial_intake")
    op.drop_index("ix_commercial_intake_tenant_id", table_name="commercial_intake")
    op.drop_table("commercial_intake")

    op.drop_index("ix_service_agreements_company_name", table_name="service_agreements")
    op.drop_index("ix_service_agreements_tenant_id", table_name="service_agreements")
    op.drop_table("service_agreements")

    op.drop_index(
        "ix_contact_language_preferences_tenant_id",
        table_name="contact_language_preferences",
    )
    op.drop_index(
        "ix_contact_language_preferences_phone_number",
        table_name="contact_language_preferences",
    )
    op.drop_table("contact_language_preferences")

    op.drop_index("ix_surge_mode_records_expires_at", table_name="surge_mode_records")
    op.drop_index("ix_surge_mode_records_tenant_id", table_name="surge_mode_records")
    op.drop_table("surge_mode_records")

    op.drop_index("ix_weather_alerts_alert_id", table_name="weather_alerts")
    op.drop_index("ix_weather_alerts_tenant_id", table_name="weather_alerts")
    op.drop_table("weather_alerts")
