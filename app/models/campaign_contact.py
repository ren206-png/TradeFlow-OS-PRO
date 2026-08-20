from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class CampaignContact(Base):
    """Phase 4: Per-contact campaign enrollment record."""

    __tablename__ = "campaign_contacts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    campaign_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contractors.id", ondelete="CASCADE"), nullable=False, index=True
    )
    recipient_phone: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    recipient_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # pending|contacted|converted|stopped|unsubscribed
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    current_step: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_contacted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    converted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    campaign: Mapped["Campaign"] = relationship("Campaign", back_populates="contacts")
