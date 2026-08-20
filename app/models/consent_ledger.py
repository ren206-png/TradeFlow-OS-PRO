from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Index, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ConsentLedger(Base):
    """
    Append-only consent records for outbound communication.
    CASL/TCPA compliance audit trail.
    NO UPDATE, NO DELETE — this table is a ledger.
    """
    __tablename__ = "consent_ledger"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    recipient_phone: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(10), nullable=False)    # sms/call/email
    # express / implied_inbound / implied_transaction
    consent_type: Mapped[str] = mapped_column(String(30), nullable=False)
    consent_basis: Mapped[str] = mapped_column(String(255), nullable=False)
    evidence_call_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    evidence_form_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    # null = never expires (express consent); set for implied (CASL 2yr window)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_consent_ledger_tenant_phone_channel", "tenant_id", "recipient_phone", "channel"),
    )
