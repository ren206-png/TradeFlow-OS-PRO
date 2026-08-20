from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Appointment(Base):
    """Phase 4: Appointment lifecycle tracking."""

    __tablename__ = "appointments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contractors.id", ondelete="CASCADE"), nullable=False, index=True
    )
    lead_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("leads.id", ondelete="SET NULL"), nullable=True, index=True
    )
    caller_phone: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    caller_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    appointment_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    job_type: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    # scheduled|confirmed|cancelled|completed|no_show
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="scheduled")
    fsm_appointment_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    confirmation_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    reminder_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    reschedule_offered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
