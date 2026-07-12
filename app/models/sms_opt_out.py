from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SmsOptOut(Base):
    """
    Tracks phone numbers that have opted out of SMS via STOP keyword.
    Checked before every outbound SMS send.
    """
    __tablename__ = "sms_opt_outs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    phone: Mapped[str] = mapped_column(String(30), nullable=False, unique=True, index=True)
    opted_out_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    opted_back_in_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # is_opted_out = True means blocked; False means re-subscribed via UNSTOP/START
    is_opted_out: Mapped[bool] = mapped_column(default=True, nullable=False)


class SmsConsent(Base):
    """
    Records implied consent for SMS per contact (inbound caller = consent).
    Stores the call_id that established consent for audit purposes.
    """
    __tablename__ = "sms_consents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    phone: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    source_call_id: Mapped[str] = mapped_column(String(128), nullable=False)
    consent_basis: Mapped[str] = mapped_column(String(50), nullable=False, default="inbound_caller")
    consented_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    first_sms_sent: Mapped[bool] = mapped_column(default=False, nullable=False)
