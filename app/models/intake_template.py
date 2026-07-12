from __future__ import annotations

import datetime
from typing import Optional

from sqlalchemy import Boolean, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class IntakeTemplate(Base):
    __tablename__ = "intake_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    contractor_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("contractors.id"), nullable=True
    )  # NULL = system template
    trade: Mapped[str] = mapped_column(String(100))  # "plumbing", "hvac", "electrical", etc.
    is_system: Mapped[bool] = mapped_column(Boolean, default=True)  # system vs custom
    questions: Mapped[list] = mapped_column(JSON)  # list of question dicts
    created_at: Mapped[datetime.datetime] = mapped_column(default=datetime.datetime.utcnow)
