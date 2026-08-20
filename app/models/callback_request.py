from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class CallbackRequest(Base):
    """
    Tracks webform callback and lead-ingest callback requests.
    Used for idempotency, phone-abuse detection, and speed-to-lead metrics.
    """
    __tablename__ = "callback_requests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    caller_phone: Mapped[str] = mapped_column(String(30), nullable=False)
    form_submission_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, unique=True)
    name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    issue: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(128), nullable=False, default="webhook")
    # pending / calling / completed / scheduled / failed
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    scheduled_for: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    outbound_call_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    lead_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("leads.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=True
    )

    __table_args__ = (
        Index("ix_callback_requests_tenant_phone", "tenant_id", "caller_phone"),
        Index("ix_callback_requests_caller_phone", "caller_phone"),
    )
