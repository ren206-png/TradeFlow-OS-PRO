from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class RevenueAttributionLedger(Base):
    """
    Phase 4: Append-only revenue attribution ledger.
    NO UPDATE, NO DELETE — corrections are new rows with is_correction=True referencing original_id.
    All monetary values MUST be integer cents. Never float.
    is_estimated=True unless value verified via FSM.
    """

    __tablename__ = "revenue_attribution_ledger"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contractors.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # estimate_converted|appointment_booked|reactivation_booked
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    lead_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("leads.id", ondelete="SET NULL"), nullable=True
    )
    estimate_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("estimates.id", ondelete="SET NULL"), nullable=True
    )
    appointment_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("appointments.id", ondelete="SET NULL"), nullable=True
    )
    campaign_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    campaign_step: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Integer cents ONLY — no floats ever
    attributed_value_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    # CAD|USD|MXN
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    # True unless verified via FSM — label always propagates to dashboard
    is_estimated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    # Correction pattern: append-only, reference original row
    original_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    is_correction: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # NO updated_at — this is an append-only ledger. No UPDATE. No DELETE.
