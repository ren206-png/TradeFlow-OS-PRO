from __future__ import annotations
import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.memory import MemoryJobStore

from app.config import settings

logger = logging.getLogger(__name__)

_scheduler = AsyncIOScheduler(
    jobstores={"default": MemoryJobStore()},
    timezone="UTC",
)


def get_scheduler() -> AsyncIOScheduler:
    return _scheduler


def start_scheduler() -> None:
    if not _scheduler.running:
        _scheduler.start()
        logger.info("APScheduler started.")


def shutdown_scheduler() -> None:
    if _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("APScheduler stopped.")


# ---------------------------------------------------------------------------
# Job: missed call recovery outbound call (fires 15 min after missed call)
# ---------------------------------------------------------------------------

def schedule_missed_call_recovery(
    contractor_id: str,
    to_number: str,
    from_number: str,
    lead_id: str,
) -> None:
    fire_at = datetime.now(tz=timezone.utc) + timedelta(minutes=15)
    _scheduler.add_job(
        _missed_call_recovery_job,
        trigger="date",
        run_date=fire_at,
        kwargs={
            "contractor_id": contractor_id,
            "to_number": to_number,
            "from_number": from_number,
            "lead_id": lead_id,
        },
        id=f"missed_call_{lead_id}",
        replace_existing=True,
    )
    logger.info("Missed call recovery scheduled for %s | lead=%s", fire_at.isoformat(), lead_id)


async def _missed_call_recovery_job(
    contractor_id: str, to_number: str, from_number: str, lead_id: str
) -> None:
    from app.database import async_session_factory
    from app.models.contractor import Contractor
    from app.services.retell_client import RetellClient
    from sqlalchemy import select
    import uuid

    logger.info("Firing missed call recovery outbound | lead=%s to=%s", lead_id, to_number)
    client = RetellClient()
    try:
        agent_id: str | None = None
        async with async_session_factory() as db:
            result = await db.execute(select(Contractor).where(Contractor.id == uuid.UUID(contractor_id)))
            contractor = result.scalar_one_or_none()
            if contractor:
                agent_id = contractor.retell_agent_id

        await client.create_phone_call(
            to_number=to_number,
            from_number=from_number,
            agent_id=agent_id,
            metadata={
                "contractor_id": contractor_id,
                "lead_id": lead_id,
                "call_type": "missed_call_recovery",
            },
        )
    except Exception as exc:
        logger.error("Missed call outbound failed | lead=%s error=%s", lead_id, exc)


# ---------------------------------------------------------------------------
# Job: appointment reminder SMS (fires 24 hours before appointment)
# ---------------------------------------------------------------------------

def schedule_appointment_reminder(
    contractor_id: str,
    lead_id: str,
    phone: str,
    appointment_time: datetime,
    service_address: str,
) -> None:
    fire_at = appointment_time - timedelta(hours=24)
    if fire_at <= datetime.now(tz=timezone.utc):
        logger.debug("Reminder fire_at is in the past; skipping | lead=%s", lead_id)
        return

    _scheduler.add_job(
        _appointment_reminder_job,
        trigger="date",
        run_date=fire_at,
        kwargs={
            "contractor_id": contractor_id,
            "lead_id": lead_id,
            "phone": phone,
            "appointment_time": appointment_time.isoformat(),
            "service_address": service_address,
        },
        id=f"reminder_{lead_id}",
        replace_existing=True,
    )
    logger.info("Appointment reminder scheduled for %s | lead=%s", fire_at.isoformat(), lead_id)


async def _appointment_reminder_job(
    contractor_id: str,
    lead_id: str,
    phone: str,
    appointment_time: str,
    service_address: str,
) -> None:
    from app.database import async_session_factory
    from app.models.contractor import Contractor
    from app.services.sms import SMSService
    from sqlalchemy import select
    import uuid

    async with async_session_factory() as db:
        result = await db.execute(select(Contractor).where(Contractor.id == uuid.UUID(contractor_id)))
        contractor = result.scalar_one_or_none()
        if not contractor:
            logger.warning("Reminder job: contractor %s not found", contractor_id)
            return

        sms = SMSService(contractor)
        await sms.send(
            to_number=phone,
            message_type="reminder",
            appointment_time=appointment_time,
            service_address=service_address,
        )
        logger.info("Reminder SMS sent | lead=%s", lead_id)


# ---------------------------------------------------------------------------
# Job: review request SMS (fires 2 hours after estimated job completion)
# ---------------------------------------------------------------------------

def schedule_review_request(
    contractor_id: str,
    lead_id: str,
    phone: str,
    appointment_time: datetime,
) -> None:
    fire_at = appointment_time + timedelta(hours=2)
    if fire_at <= datetime.now(tz=timezone.utc):
        return

    _scheduler.add_job(
        _review_request_job,
        trigger="date",
        run_date=fire_at,
        kwargs={"contractor_id": contractor_id, "lead_id": lead_id, "phone": phone},
        id=f"review_{lead_id}",
        replace_existing=True,
    )
    logger.info("Review request scheduled for %s | lead=%s", fire_at.isoformat(), lead_id)


async def _review_request_job(contractor_id: str, lead_id: str, phone: str) -> None:
    from app.database import async_session_factory
    from app.models.contractor import Contractor
    from app.models.lead import Lead
    from app.services.sms import SMSService
    from sqlalchemy import select
    import uuid

    async with async_session_factory() as db:
        lead_result = await db.execute(select(Lead).where(Lead.id == uuid.UUID(lead_id)))
        lead = lead_result.scalar_one_or_none()
        if not lead or lead.appointment_status != "booked":
            return

        contractor_result = await db.execute(
            select(Contractor).where(Contractor.id == uuid.UUID(contractor_id))
        )
        contractor = contractor_result.scalar_one_or_none()
        if not contractor or not contractor.review_link:
            return

        sms = SMSService(contractor)
        await sms.send(to_number=phone, message_type="review_request")
        logger.info("Review request SMS sent | lead=%s", lead_id)


# ---------------------------------------------------------------------------
# Job: unbooked lead follow-up SMS (fires 24 hours after call if not booked)
# ---------------------------------------------------------------------------

def schedule_unbooked_followup(
    contractor_id: str,
    lead_id: str,
    phone: str,
) -> None:
    fire_at = datetime.now(tz=timezone.utc) + timedelta(hours=24)
    _scheduler.add_job(
        _unbooked_followup_job,
        trigger="date",
        run_date=fire_at,
        kwargs={"contractor_id": contractor_id, "lead_id": lead_id, "phone": phone},
        id=f"unbooked_{lead_id}",
        replace_existing=True,
    )
    logger.info("Unbooked follow-up scheduled for %s | lead=%s", fire_at.isoformat(), lead_id)


async def _unbooked_followup_job(contractor_id: str, lead_id: str, phone: str) -> None:
    from app.database import async_session_factory
    from app.models.contractor import Contractor
    from app.models.lead import Lead
    from app.services.sms import SMSService
    from sqlalchemy import select
    import uuid

    async with async_session_factory() as db:
        lead_result = await db.execute(select(Lead).where(Lead.id == uuid.UUID(lead_id)))
        lead = lead_result.scalar_one_or_none()
        if not lead or lead.appointment_status != "not_booked":
            logger.debug("Unbooked follow-up skipped — lead status changed | lead=%s", lead_id)
            return

        contractor_result = await db.execute(
            select(Contractor).where(Contractor.id == uuid.UUID(contractor_id))
        )
        contractor = contractor_result.scalar_one_or_none()
        if not contractor:
            return

        sms = SMSService(contractor)
        await sms.send(to_number=phone, message_type="missed_call")
        logger.info("Unbooked follow-up SMS sent | lead=%s", lead_id)
