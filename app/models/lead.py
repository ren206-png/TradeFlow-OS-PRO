from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    contractor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contractors.id", ondelete="CASCADE"), nullable=False, index=True
    )
    call_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    caller_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    service_address: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    city: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    province_state: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    postal_zip: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    property_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    business_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    trade: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    service_category: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    problem_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    emergency_level: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    life_safety_risk: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    service_area_status: Mapped[str] = mapped_column(String(20), nullable=False, default="unknown")
    appointment_status: Mapped[str] = mapped_column(String(30), nullable=False, default="not_booked")
    appointment_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    calendar_event_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    sms_confirmation_sent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    human_transfer_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    transfer_reason: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    emergency_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    revenue_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    close_probability: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    priority_level: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    customer_sentiment: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ai_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    sentiment: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    follow_up_recommended: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    follow_up_sent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    review_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    call_direction: Mapped[str] = mapped_column(String(30), nullable=False, default="inbound")
    lead_source: Mapped[str] = mapped_column(String(100), nullable=False, default="retell_call")
    recording_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    transcript_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    raw_transcript: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    contractor: Mapped[Contractor] = relationship("Contractor", back_populates="leads")
