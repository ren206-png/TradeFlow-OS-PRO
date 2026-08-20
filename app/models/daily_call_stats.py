from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class DailyCallStats(Base):
    """
    Phase 5: Nightly aggregation table — dashboard reads ONLY from this table.
    Never scan raw call tables on page load.
    """

    __tablename__ = "daily_call_stats"
    __table_args__ = (
        UniqueConstraint("tenant_id", "stat_date", name="uq_daily_call_stats_tenant_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("contractors.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    stat_date: Mapped[date] = mapped_column(Date, nullable=False)

    calls_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    calls_answered: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    calls_after_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    calls_booked: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    calls_abandoned: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    calls_transferred: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    calls_spam_blocked: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    avg_duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    avg_time_to_answer_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Integer percentage 0–100. Never float.
    booking_rate_pct: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Integer cents — no floats ever
    estimated_revenue_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="CAD")
    # Always labeled — never strip this field
    is_estimated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    missed_calls_recovered: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reminders_sent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    no_shows_prevented: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), nullable=True
    )
