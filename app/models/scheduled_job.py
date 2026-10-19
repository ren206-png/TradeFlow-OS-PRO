from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ScheduledJob(Base):
    """A one-off delayed job (follow-up text, appointment reminder...) that must survive an app restart."""
    __tablename__ = "scheduled_jobs"

    id: Mapped[str] = mapped_column(String(160), primary_key=True)  # the APScheduler job id
    func_name: Mapped[str] = mapped_column("func", String(64), nullable=False)
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    kwargs: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", server_default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
