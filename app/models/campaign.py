from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Campaign(Base):
    """Phase 4: Reactivation / estimate follow-up / seasonal campaigns."""

    __tablename__ = "campaigns"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contractors.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # reactivation|estimate_followup|seasonal
    campaign_type: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # active|paused|completed|cancelled
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="active")
    trade: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    # fall|spring|winter|summer
    season: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    # Per-tenant per-campaign daily send cap
    daily_send_cap: Mapped[int] = mapped_column(Integer, nullable=False, default=50)
    total_sent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_converted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    contacts: Mapped[list["CampaignContact"]] = relationship(
        "CampaignContact", back_populates="campaign", lazy="select"
    )
