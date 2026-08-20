from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Index, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class OutboundLedger(Base):
    """
    Append-only log of every outbound communication attempt.
    NO UPDATE, NO DELETE — this table is a ledger.
    """
    __tablename__ = "outbound_ledger"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    recipient_phone: Mapped[str] = mapped_column(String(30), nullable=False)
    channel: Mapped[str] = mapped_column(String(10), nullable=False)   # sms/call/email
    status: Mapped[str] = mapped_column(String(20), nullable=False)    # sent/blocked/failed
    block_reason: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    template_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    campaign_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    # First 160 chars only — no PII stored in preview
    message_preview: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_outbound_ledger_tenant_created", "tenant_id", "created_at"),
    )
