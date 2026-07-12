from __future__ import annotations

import datetime
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.contractor import Contractor
from app.models.on_call_schedule import OnCallSchedule

logger = logging.getLogger(__name__)


class OnCallService:
    async def get_transfer_number(self, contractor: Contractor, db: AsyncSession) -> str | None:
        """Get the on-call number for right now, falling back to calendar_config transfer_number."""
        now = datetime.datetime.utcnow()  # use contractor.timezone ideally, utcnow for MVP
        day = now.weekday()  # 0=Mon
        time_str = now.strftime("%H:%M:%S")

        result = await db.execute(
            select(OnCallSchedule).where(
                OnCallSchedule.contractor_id == contractor.id,
                OnCallSchedule.day_of_week == day,
                OnCallSchedule.is_active == True,
                OnCallSchedule.start_time <= time_str,
                OnCallSchedule.end_time >= time_str,
            )
        )
        schedule = result.scalar_one_or_none()
        if schedule:
            return schedule.phone_number

        # Fall back to calendar_config
        if contractor.calendar_config:
            return contractor.calendar_config.get("transfer_number")
        return None

    async def send_missed_transfer_alert(
        self,
        contractor: Contractor,
        call_id: str,
        recording_url: str | None,
        db: AsyncSession,
    ) -> None:
        """Send SMS alert to contractor when a transfer is not answered."""
        from app.services.sms import SMSService

        if not contractor.sms_enabled:
            return

        owner_phone = None
        if contractor.calendar_config:
            owner_phone = contractor.calendar_config.get("owner_phone") or contractor.calendar_config.get("transfer_number")

        if not owner_phone:
            return

        recording_part = f"\nRecording: {recording_url}" if recording_url else ""
        message = (
            f"MISSED TRANSFER -- {contractor.name}\n"
            f"Call ID: {call_id}\n"
            f"A caller was transferred but no one answered.{recording_part}\n"
            f"Please call them back ASAP."
        )
        sms = SMSService(contractor)
        try:
            await sms._send_async(to=owner_phone, body=message, message_type="missed_transfer_alert")
        except Exception as exc:
            logger.warning("send_missed_transfer_alert failed | contractor=%s error=%s", contractor.name, exc)
