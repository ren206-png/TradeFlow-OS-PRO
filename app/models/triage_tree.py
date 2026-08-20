"""
TriageTree — append-only versioned triage decision trees per tenant/trade.
New version = new row. Never update existing rows.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class TriageTree(Base):
    __tablename__ = "triage_trees"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # null tenant_id = system/seed tree available to all tenants
    tenant_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("contractors.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    trade: Mapped[str] = mapped_column(String(50), nullable=False)  # plumbing|hvac|electrical|commercial_mechanical|general
    version: Mapped[int] = mapped_column(Integer, nullable=False)   # monotonically increasing per trade
    author_credential: Mapped[str] = mapped_column(String(255), nullable=False)
    jurisdiction: Mapped[str] = mapped_column(String(50), nullable=False, default="ALL")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    language: Mapped[str] = mapped_column(String(8), nullable=False, default="en", server_default="en")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # NO updated_at — append-only; new version = new row
