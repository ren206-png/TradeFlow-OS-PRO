from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class PageEvent(Base):
    """
    Lightweight landing-page event log.
    Fired by the JS tracker on tradesflowos.com for key conversion actions.

    event_name examples:
        page_view, cta_hero_click, cta_sticky_click, demo_call_click,
        exit_modal_shown, exit_modal_cta_click, scroll_25, scroll_50,
        scroll_75, scroll_100, signup_start, signup_complete
    """
    __tablename__ = "page_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # anonymous session id generated client-side (localStorage), not PII
    session_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # page path — useful when we add more pages later
    page: Mapped[str] = mapped_column(String(255), nullable=False, default="/")
    # referrer (truncated to 512 chars)
    referrer: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    # coarse device type derived from user-agent server-side
    device: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown")
    # A/B test variant ("A" or "B") — nullable for pre-test events
    ab_variant: Mapped[str] = mapped_column(String(2), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
