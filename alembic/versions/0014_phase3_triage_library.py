"""Phase 3: Journeyman Triage Library & Safety Coaching Engine

Creates:
  - triage_trees table (append-only versioned trees)
  - triage_nodes table
  - coaching_scripts table
  - safety_action_ledger table (append-only audit trail)
  - on_call_rotation table (per-tenant tech rotation)
  - ADD COLUMN triage_library_v2 BOOLEAN DEFAULT FALSE to contractors
  - ADD COLUMN safety_coaching_enabled BOOLEAN DEFAULT FALSE to contractors
  - ADD COLUMN primary_trade VARCHAR(50) to contractors

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-19
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # triage_trees — append-only versioned triage trees
    # ------------------------------------------------------------------
    op.create_table(
        "triage_trees",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("contractors.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("trade", sa.String(50), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("author_credential", sa.String(255), nullable=False),
        sa.Column("jurisdiction", sa.String(50), nullable=False, server_default="ALL"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("language", sa.String(8), nullable=False, server_default="en"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        # NO updated_at — append-only
    )
    op.create_index("ix_triage_trees_tenant_id", "triage_trees", ["tenant_id"])
    op.create_index("ix_triage_trees_trade", "triage_trees", ["trade"])
    op.create_index(
        "ix_triage_trees_tenant_trade_active",
        "triage_trees",
        ["tenant_id", "trade", "is_active"],
    )

    # ------------------------------------------------------------------
    # triage_nodes
    # ------------------------------------------------------------------
    op.create_table(
        "triage_nodes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tree_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("triage_trees.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("node_key", sa.String(128), nullable=False),
        sa.Column("question_text", sa.String(1024), nullable=False),
        sa.Column("question_text_fr", sa.String(1024), nullable=True),
        sa.Column("urgency_level", sa.String(30), nullable=False, server_default="standard"),
        sa.Column("urgency_escalation_trigger", sa.String(512), nullable=True),
        sa.Column("coaching_script_id", sa.String(128), nullable=True),
        sa.Column("next_node_booked", sa.String(128), nullable=True),
        sa.Column("next_node_key_map", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("is_terminal", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_triage_nodes_tree_id", "triage_nodes", ["tree_id"])
    op.create_index(
        "ix_triage_nodes_tree_node_key",
        "triage_nodes",
        ["tree_id", "node_key"],
        unique=True,
    )

    # ------------------------------------------------------------------
    # coaching_scripts
    # ------------------------------------------------------------------
    op.create_table(
        "coaching_scripts",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("script_text", sa.Text(), nullable=False),
        sa.Column("script_text_fr", sa.Text(), nullable=True),
        sa.Column("trade", sa.String(50), nullable=False),
        sa.Column("scenario", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_coaching_scripts_trade", "coaching_scripts", ["trade"])

    # ------------------------------------------------------------------
    # safety_action_ledger — append-only audit trail
    # ------------------------------------------------------------------
    op.create_table(
        "safety_action_ledger",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("call_id", sa.String(128), nullable=False),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("contractors.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("node_key", sa.String(128), nullable=False),
        sa.Column("script_id", sa.String(128), nullable=False),
        sa.Column("script_text_delivered", sa.Text(), nullable=False),
        sa.Column("language", sa.String(8), nullable=False, server_default="en"),
        sa.Column("urgency_level", sa.String(30), nullable=False),
        sa.Column(
            "delivered_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        # NO updated_at — append-only; NO UPDATE, NO DELETE
    )
    op.create_index("ix_safety_action_ledger_call_id", "safety_action_ledger", ["call_id"])
    op.create_index("ix_safety_action_ledger_tenant_id", "safety_action_ledger", ["tenant_id"])

    # ------------------------------------------------------------------
    # on_call_rotation
    # ------------------------------------------------------------------
    op.create_table(
        "on_call_rotation",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("contractors.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tech_name", sa.String(255), nullable=False),
        sa.Column("phone_number", sa.String(30), nullable=False),
        sa.Column("day_of_week", sa.Integer(), nullable=False),
        sa.Column("start_hour", sa.Integer(), nullable=False),
        sa.Column("end_hour", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_on_call_rotation_tenant_id", "on_call_rotation", ["tenant_id"])
    op.create_index(
        "ix_on_call_rotation_tenant_day",
        "on_call_rotation",
        ["tenant_id", "day_of_week"],
    )

    # ------------------------------------------------------------------
    # ADD COLUMNS to contractors (additive only)
    # ------------------------------------------------------------------
    op.add_column(
        "contractors",
        sa.Column("triage_library_v2", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "contractors",
        sa.Column("safety_coaching_enabled", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "contractors",
        sa.Column("primary_trade", sa.String(50), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("contractors", "primary_trade")
    op.drop_column("contractors", "safety_coaching_enabled")
    op.drop_column("contractors", "triage_library_v2")
    op.drop_index("ix_on_call_rotation_tenant_day", table_name="on_call_rotation")
    op.drop_index("ix_on_call_rotation_tenant_id", table_name="on_call_rotation")
    op.drop_table("on_call_rotation")
    op.drop_index("ix_safety_action_ledger_tenant_id", table_name="safety_action_ledger")
    op.drop_index("ix_safety_action_ledger_call_id", table_name="safety_action_ledger")
    op.drop_table("safety_action_ledger")
    op.drop_index("ix_coaching_scripts_trade", table_name="coaching_scripts")
    op.drop_table("coaching_scripts")
    op.drop_index("ix_triage_nodes_tree_node_key", table_name="triage_nodes")
    op.drop_index("ix_triage_nodes_tree_id", table_name="triage_nodes")
    op.drop_table("triage_nodes")
    op.drop_index("ix_triage_trees_tenant_trade_active", table_name="triage_trees")
    op.drop_index("ix_triage_trees_trade", table_name="triage_trees")
    op.drop_index("ix_triage_trees_tenant_id", table_name="triage_trees")
    op.drop_table("triage_trees")
