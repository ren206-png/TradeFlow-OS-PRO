from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Integer, JSON, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class CommercialIntake(Base):
    __tablename__ = "commercial_intake"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    lead_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    site_contact_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    site_contact_phone: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    account_contact_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    po_number: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    building_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    unit_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    equipment_tag_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    service_agreement_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    # 0-100; must be >= 80 to trigger acknowledgment
    match_confidence: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    pipefield_payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
