from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import Boolean, DateTime, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SurgeModeRecord(Base):
    __tablename__ = "surge_mode_records"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    alert_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    surge_type: Mapped[str] = mapped_column(String(30), nullable=False)
    activated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deactivated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    is_manual: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Stored as Numeric(3,1); compared as int*10 to avoid floats. Max 1.5 enforced in service.
    overbooking_multiplier: Mapped[Decimal] = mapped_column(
        Numeric(3, 1), nullable=False, default=Decimal("1.0")
    )
    activated_by_alert_title: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
