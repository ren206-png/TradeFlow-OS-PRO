from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, JSON, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class WeatherAlert(Base):
    __tablename__ = "weather_alerts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        # FK checked at migration level; avoid circular import at model level
        nullable=False,
        index=True,
    )
    alert_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    surge_type: Mapped[str] = mapped_column(String(30), nullable=False)  # extreme_cold|heat|storm
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)  # environment_canada|nws
    postal_codes: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    raw_payload: Mapped[dict] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
