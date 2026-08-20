"""
CoachingScript — fixed allowlist of reviewed safety coaching scripts.
The LLM may only SELECT a script_id. It may NEVER generate coaching text freeform.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class CoachingScript(Base):
    __tablename__ = "coaching_scripts"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)  # human-readable key
    script_text: Mapped[str] = mapped_column(Text, nullable=False)
    script_text_fr: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    trade: Mapped[str] = mapped_column(String(50), nullable=False)
    scenario: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # NO freeform LLM generation ever touches this table — LLM only selects the id
