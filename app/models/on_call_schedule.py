import uuid

from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class OnCallSchedule(Base):
    __tablename__ = "on_call_schedules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    contractor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("contractors.id"))
    day_of_week: Mapped[int] = mapped_column(Integer)  # 0=Mon, 6=Sun
    start_time: Mapped[str] = mapped_column(String(8))  # "08:00:00" HH:MM:SS
    end_time: Mapped[str] = mapped_column(String(8))    # "18:00:00"
    phone_number: Mapped[str] = mapped_column(String(30))  # on-call tech number
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
