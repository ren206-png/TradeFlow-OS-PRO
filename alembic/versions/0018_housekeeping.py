"""Housekeeping: create page_events, demo_calls; add password reset token to contractors; add missing indexes

Revision ID: 0018
Revises: 0017
Create Date: 2026-08-20

Fixes:
  - page_events was never explicitly created in an Alembic migration (only
    via Base.metadata.create_all). Use IF NOT EXISTS so existing prod DBs
    are unaffected.
  - demo_calls same situation.
  - Migration 0011 adds ab_variant to page_events — this migration ensures
    the table exists before that column is added on any fresh DB. On existing
    DBs the IF NOT EXISTS is a no-op.
  - Add password_reset_token + password_reset_token_expires_at to contractors
    for secure single-use reset tokens.
  - Add missing indexes on leads.appointment_status and
    leads(contractor_id, appointment_status).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # page_events — create if not already present (was only created via
    # Base.metadata.create_all in earlier deploys, not via Alembic)
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS page_events (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            event_name VARCHAR(64) NOT NULL,
            session_id VARCHAR(64) NOT NULL,
            page VARCHAR(255) NOT NULL DEFAULT '/',
            referrer VARCHAR(512) NOT NULL DEFAULT '',
            device VARCHAR(16) NOT NULL DEFAULT 'unknown',
            ab_variant VARCHAR(2),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_page_events_event_name ON page_events (event_name)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_page_events_session_id ON page_events (session_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_page_events_created_at ON page_events (created_at)"
    )

    # ------------------------------------------------------------------
    # demo_calls — create if not already present
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS demo_calls (
            id SERIAL PRIMARY KEY,
            retell_call_id VARCHAR(128) NOT NULL UNIQUE,
            from_number VARCHAR(32) NOT NULL,
            duration_seconds INTEGER NOT NULL DEFAULT 0,
            started_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_demo_calls_retell_call_id ON demo_calls (retell_call_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_demo_calls_from_number ON demo_calls (from_number)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_demo_calls_started_at ON demo_calls (started_at)"
    )

    # ------------------------------------------------------------------
    # contractors — add password reset token columns for secure single-use
    # reset flow (replaces the deterministic SHA-256 time-based token)
    # ------------------------------------------------------------------
    op.execute(
        "ALTER TABLE contractors ADD COLUMN IF NOT EXISTS "
        "reset_token VARCHAR(128)"
    )
    op.execute(
        "ALTER TABLE contractors ADD COLUMN IF NOT EXISTS "
        "reset_token_expires_at TIMESTAMPTZ"
    )

    # ------------------------------------------------------------------
    # leads — add missing indexes for appointment_status filter queries
    # ------------------------------------------------------------------
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_leads_appointment_status "
        "ON leads (appointment_status)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_leads_contractor_appointment "
        "ON leads (contractor_id, appointment_status)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_leads_contractor_appointment")
    op.execute("DROP INDEX IF EXISTS ix_leads_appointment_status")

    op.execute(
        "ALTER TABLE contractors DROP COLUMN IF EXISTS reset_token_expires_at"
    )
    op.execute(
        "ALTER TABLE contractors DROP COLUMN IF EXISTS reset_token"
    )

    op.execute("DROP INDEX IF EXISTS ix_demo_calls_started_at")
    op.execute("DROP INDEX IF EXISTS ix_demo_calls_from_number")
    op.execute("DROP INDEX IF EXISTS ix_demo_calls_retell_call_id")
    op.execute("DROP TABLE IF EXISTS demo_calls")

    op.execute("DROP INDEX IF EXISTS ix_page_events_created_at")
    op.execute("DROP INDEX IF EXISTS ix_page_events_session_id")
    op.execute("DROP INDEX IF EXISTS ix_page_events_event_name")
    op.execute("DROP TABLE IF EXISTS page_events")
