"""
DemoCall — tracks every call made to the Summit Plumbing Demo tenant.
Used for daily-cap enforcement and admin analytics.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class DemoCall(Base):
    __tablename__ = "demo_calls"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    retell_call_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    from_number: Mapped[str] = mapped_column(String(32), index=True)
    duration_seconds: Mapped[int] = mapped_column(default=0)
    started_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(tz=timezone.utc), index=True
    )
