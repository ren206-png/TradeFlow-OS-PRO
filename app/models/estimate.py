from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Estimate(Base):
    """Phase 4: Estimate follow-up drip tracking. Money in integer cents only."""

    __tablename__ = "estimates"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contractors.id", ondelete="CASCADE"), nullable=False, index=True
    )
    lead_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("leads.id", ondelete="SET NULL"), nullable=True, index=True
    )
    caller_phone: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    caller_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # Integer cents ONLY — no floats ever
    estimate_value_cents: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # CAD|USD|MXN
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="CAD")
    # draft|sent|viewed|accepted|declined|expired
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="draft")
    fsm_estimate_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # manual|jobber|hcp|csv
    source: Mapped[str] = mapped_column(String(50), nullable=False, default="manual")
    followup_enrolled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    followup_step: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    followup_paused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
