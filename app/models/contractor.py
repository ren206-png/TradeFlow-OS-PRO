from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, JSON, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Contractor(Base):
    __tablename__ = "contractors"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    agent_name: Mapped[str] = mapped_column(String(100), nullable=False)
    phone_number: Mapped[str] = mapped_column(String(30), nullable=False, unique=True)
    api_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    trades: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    service_areas: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="America/New_York")
    diagnostic_fee: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    free_estimate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    calendar_provider: Mapped[str] = mapped_column(String(50), nullable=False, default="manual")
    calendar_config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    sms_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    review_link: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    retell_agent_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    leads: Mapped[list[Lead]] = relationship("Lead", back_populates="contractor", lazy="select")
    call_sessions: Mapped[list[CallSession]] = relationship(
        "CallSession", back_populates="contractor", lazy="select"
    )
