from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class A2PRegistration(Base):
    """
    A2P 10DLC registration status per tenant.
    One row per tenant (unique constraint on tenant_id).
    Status is mutable (unlike ledger tables) — this tracks current registration state.
    """
    __tablename__ = "a2p_registration"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # unique: one registration record per tenant
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    brand_registration_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    campaign_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    # unregistered / pending / approved / rejected / suspended
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="unregistered")
    submitted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    rejection_reason: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
