"""
TriageNode — individual nodes within a triage decision tree.
Append-only: modifications = new tree version.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, JSON, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class TriageNode(Base):
    __tablename__ = "triage_nodes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tree_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("triage_trees.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # unique within a tree — e.g. "root", "has_hot_water", "burst_pipe"
    node_key: Mapped[str] = mapped_column(String(128), nullable=False)
    question_text: Mapped[str] = mapped_column(String(1024), nullable=False)
    question_text_fr: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    urgency_level: Mapped[str] = mapped_column(
        String(30), nullable=False, default="standard"
    )  # emergency_911 | urgent | standard | low
    urgency_escalation_trigger: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    # coaching_script_id references coaching_scripts.id (not a FK — allowlist enforced at app layer)
    coaching_script_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    next_node_booked: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    # {"answer_pattern": "next_node_key"} — routing map
    next_node_key_map: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    is_terminal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
