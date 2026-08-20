from __future__ import annotations
import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

from app.config import settings

logger = logging.getLogger(__name__)

# Convert async DB URL to sync for APScheduler's SQLAlchemy jobstore
_sync_db_url = (
    settings.database_url
    .replace("postgresql+asyncpg://", "postgresql://")
    .replace("sqlite+aiosqlite://", "sqlite:///")
)

_scheduler = AsyncIOScheduler(
    jobstores={"default": SQLAlchemyJobStore(url=_sync_db_url)},
    timezone="UTC",
)


def get_scheduler() -> AsyncIOScheduler:
    return _scheduler


def start_scheduler() -> None:
    if not _scheduler.running:
        _scheduler.start()
        # Daily call quality digest — 8:00 AM UTC every day
        _scheduler.add_job(
            _daily_digest_job,
            trigger="cron",
            hour=8,
            minute=0,
            id="daily_quality_digest",
            replace_existing=True,
        )
        logger.info("APScheduler started. Daily digest scheduled at 08:00 UTC.")
        # Phase 6: Weather surge polling + expiry — every 30 minutes when flag ON
        if settings.weather_surge_mode:
            _scheduler.add_job(
                _weather_surge_poll_and_expire_job,
                trigger="interval",
                minutes=30,
                id="weather_surge_poll_expire",
                replace_existing=True,
            )
            logger.info("Phase 6: Weather surge poll + expiry job scheduled every 30 minutes.")
        # Phase 5: nightly stats aggregation at 07:00 UTC (safe default for 02:00 local)
        if settings.owner_dashboard_v2:
            _scheduler.add_job(
                _nightly_aggregation_job,
                trigger="cron",
                hour=7,
                minute=0,
                id="nightly_stats_aggregation",
                replace_existing=True,
            )
            logger.info("Phase 5: Nightly stats aggregation scheduled at 07:00 UTC.")
            # Monthly summary email: first day of month at 08:00 UTC
            _scheduler.add_job(
                _monthly_summary_email_job,
                trigger="cron",
                day=1,
                hour=8,
                minute=0,
                id="monthly_summary_email",
                replace_existing=True,
            )
            logger.info("Phase 5: Monthly summary email scheduled on day 1 at 08:00 UTC.")


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
        async with async_session_factory() as db:
            from app.services.billing import BillingService
            result = await db.execute(select(Contractor).where(Contractor.id == uuid.UUID(contractor_id)))
            contractor = result.scalar_one_or_none()
            if not contractor:
                logger.warning("Recovery job: contractor %s not found", contractor_id)
                return

            # Hard cap check before firing outbound call
            usage = await BillingService().check_usage_limit(contractor, "calls")
            if not usage["allowed"]:
                logger.warning(
                    "Recovery call blocked — plan cap reached | contractor=%s used=%d limit=%d",
                    contractor.name, usage["used"], usage["limit"],
                )
                return

            agent_id = contractor.retell_agent_id
            await client.create_phone_call(
                to_number=to_number,
                from_number=from_number,
                override_agent_id=agent_id,
                metadata={
                    "contractor_id": contractor_id,
                    "lead_id": lead_id,
                    "call_type": "missed_call_recovery",
                },
            )
            # Count outbound recovery call against quota
            await BillingService().increment_usage(contractor, "calls", db)
            await db.commit()
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
        from app.models.lead import Lead
        result = await db.execute(select(Contractor).where(Contractor.id == uuid.UUID(contractor_id)))
        contractor = result.scalar_one_or_none()
        if not contractor:
            logger.warning("Reminder job: contractor %s not found", contractor_id)
            return

        lead_result = await db.execute(select(Lead).where(Lead.id == uuid.UUID(lead_id)))
        lead = lead_result.scalar_one_or_none()

        sms = SMSService(contractor).with_db(db)
        # Phase 1 fix (Risk #3): use _send_compliant to enforce opt-out checks
        await sms._send_compliant(
            to=phone,
            body=(
                f"Hi {(lead.caller_name if lead else None) or 'there'}, reminder: "
                f"your appointment is tomorrow, {appointment_time[:10]} at {appointment_time[11:16]}."
            ),
            message_type="appointment_reminder",
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

        sms = SMSService(contractor).with_db(db)
        # Phase 1 fix (Risk #3): use _send_compliant to enforce opt-out checks
        await sms._send_compliant(
            to=phone,
            body=(
                f"Hi {lead.caller_name or 'there'}, thank you for choosing us! "
                f"We'd love your feedback: {contractor.review_link or ''}"
            ),
            message_type="review_request",
        )
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

        sms = SMSService(contractor).with_db(db)
        # Phase 1 fix (Risk #3): use _send_compliant to enforce opt-out checks
        await sms._send_compliant(
            to=phone,
            body=(
                "Hi there, we wanted to check in — are you still looking for help "
                "with your service request? We're ready when you are."
            ),
            message_type="followup",
        )
        logger.info("Unbooked follow-up SMS sent | lead=%s", lead_id)


# ---------------------------------------------------------------------------
# Job: 24-hour lead follow-up SMS (fires 24 hours after lead creation if not booked)
# ---------------------------------------------------------------------------

def schedule_lead_followup(lead_id: str, contractor_id: str) -> None:
    fire_at = datetime.now(tz=timezone.utc) + timedelta(hours=24)
    _scheduler.add_job(
        _lead_followup_job,
        trigger="date",
        run_date=fire_at,
        kwargs={"lead_id": lead_id, "contractor_id": contractor_id},
        id=f"lead_followup_{lead_id}",
        replace_existing=True,
    )
    logger.info("Lead follow-up scheduled for %s | lead=%s", fire_at.isoformat(), lead_id)


async def _lead_followup_job(lead_id: str, contractor_id: str) -> None:
    from app.database import async_session_factory
    from app.models.contractor import Contractor
    from app.models.lead import Lead
    from app.services.billing import BillingService
    from app.services.sms import SMSService
    from sqlalchemy import select
    import uuid

    logger.info("Firing lead follow-up job | lead=%s contractor=%s", lead_id, contractor_id)
    try:
        async with async_session_factory() as db:
            lead_result = await db.execute(select(Lead).where(Lead.id == uuid.UUID(lead_id)))
            lead = lead_result.scalar_one_or_none()
            if not lead:
                logger.warning("Lead follow-up: lead %s not found", lead_id)
                return
            if lead.appointment_status in ("booked", "contacted"):
                logger.info(
                    "Lead follow-up skipped — status is %r | lead=%s",
                    lead.appointment_status, lead_id,
                )
                return

            contractor_result = await db.execute(
                select(Contractor).where(Contractor.id == uuid.UUID(contractor_id))
            )
            contractor = contractor_result.scalar_one_or_none()
            if not contractor:
                logger.warning("Lead follow-up: contractor %s not found", contractor_id)
                return
            if not contractor.sms_enabled:
                logger.info("Lead follow-up: SMS disabled for contractor %s", contractor_id)
                return

            sms = SMSService(contractor).with_db(db)
            # Phase 1 fix (Risk #3): use _send_compliant to enforce opt-out checks
            await sms._send_compliant(
                to=lead.phone,
                body=(
                    f"Hi {lead.caller_name or 'there'}, we wanted to check in — are you still "
                    f"looking for help with your service request? We're ready when you are."
                ),
                message_type="followup",
            )

            await BillingService().increment_usage(contractor, "sms", db)

            if hasattr(lead, "follow_up_sent"):
                lead.follow_up_sent = True
                await db.flush()
            else:
                logger.info("Lead follow-up SMS sent (follow_up_sent field not on model) | lead=%s", lead_id)

            logger.info("Lead follow-up complete | lead=%s", lead_id)
    except Exception as exc:
        logger.error("Lead follow-up job failed | lead=%s error=%s", lead_id, exc)


# ---------------------------------------------------------------------------
# Phase 4: Appointment lifecycle — confirmation + day-before reminder
# ---------------------------------------------------------------------------

def schedule_appointment_reminders(appointment_id: str) -> None:
    """
    Queue Phase 4 appointment lifecycle jobs:
    1. Immediate confirmation SMS
    2. Day-before reminder SMS (appointment_time - 24h)

    Both gated behind 'appointment_lifecycle' feature flag inside the job functions.
    """
    if not settings.appointment_lifecycle:
        logger.debug("appointment_lifecycle flag OFF — skipping lifecycle schedule | appt=%s", appointment_id)
        return

    fire_now = datetime.now(tz=timezone.utc)
    _scheduler.add_job(
        _appointment_confirmation_job,
        trigger="date",
        run_date=fire_now,
        kwargs={"appointment_id": appointment_id},
        id=f"appt_confirm_{appointment_id}",
        replace_existing=True,
    )
    logger.info("Appointment confirmation job queued immediately | appt=%s", appointment_id)

    # Reminder job time will be resolved from DB inside the job itself.
    # Queue a deferred job that fetches the appointment and schedules the reminder.
    _scheduler.add_job(
        _appointment_schedule_reminder_job,
        trigger="date",
        run_date=fire_now,
        kwargs={"appointment_id": appointment_id},
        id=f"appt_schedule_reminder_{appointment_id}",
        replace_existing=True,
    )
    logger.info("Appointment reminder scheduler queued | appt=%s", appointment_id)


async def _appointment_confirmation_job(appointment_id: str) -> None:
    """Fire Phase 4 appointment confirmation SMS via AppointmentLifecycleService."""
    from app.database import async_session_factory
    from app.models.appointment import Appointment
    from app.models.contractor import Contractor
    from app.services.appointment_lifecycle import AppointmentLifecycleService
    from sqlalchemy import select
    import uuid

    logger.info("Firing appointment confirmation job | appt=%s", appointment_id)
    try:
        async with async_session_factory() as db:
            appt_result = await db.execute(
                select(Appointment).where(Appointment.id == uuid.UUID(appointment_id))
            )
            appointment = appt_result.scalar_one_or_none()
            if not appointment:
                logger.warning("Confirmation job: appointment %s not found", appointment_id)
                return

            contractor_result = await db.execute(
                select(Contractor).where(Contractor.id == appointment.tenant_id)
            )
            contractor = contractor_result.scalar_one_or_none()
            if not contractor:
                logger.warning("Confirmation job: contractor not found | appt=%s", appointment_id)
                return

            svc = AppointmentLifecycleService()
            await svc.send_confirmation(appointment, contractor, db)
            await db.commit()
    except Exception as exc:
        logger.error("Appointment confirmation job failed | appt=%s err=%s", appointment_id, exc)


async def _appointment_schedule_reminder_job(appointment_id: str) -> None:
    """Fetch appointment time and schedule the day-before reminder job."""
    from app.database import async_session_factory
    from app.models.appointment import Appointment
    from sqlalchemy import select
    import uuid

    try:
        async with async_session_factory() as db:
            appt_result = await db.execute(
                select(Appointment).where(Appointment.id == uuid.UUID(appointment_id))
            )
            appointment = appt_result.scalar_one_or_none()
            if not appointment:
                logger.warning("Schedule reminder job: appointment %s not found", appointment_id)
                return

            appt_time = appointment.appointment_time
            if appt_time.tzinfo is None:
                from datetime import timezone as _tz
                appt_time = appt_time.replace(tzinfo=_tz.utc)

            fire_at = appt_time - timedelta(hours=24)
            if fire_at <= datetime.now(tz=timezone.utc):
                logger.debug(
                    "Appointment reminder fire_at is in the past; skipping | appt=%s", appointment_id
                )
                return

            _scheduler.add_job(
                _appointment_reminder_lifecycle_job,
                trigger="date",
                run_date=fire_at,
                kwargs={"appointment_id": appointment_id},
                id=f"appt_reminder_{appointment_id}",
                replace_existing=True,
            )
            logger.info(
                "Appointment day-before reminder scheduled for %s | appt=%s",
                fire_at.isoformat(), appointment_id,
            )
    except Exception as exc:
        logger.error("Schedule reminder job failed | appt=%s err=%s", appointment_id, exc)


async def _appointment_reminder_lifecycle_job(appointment_id: str) -> None:
    """
    Phase 4: Day-before reminder via AppointmentLifecycleService.
    Adversarial check #1: FSM re-verify inside send_reminder().
    """
    from app.database import async_session_factory
    from app.models.appointment import Appointment
    from app.models.contractor import Contractor
    from app.services.appointment_lifecycle import AppointmentLifecycleService
    from sqlalchemy import select
    import uuid

    logger.info("Firing appointment day-before reminder | appt=%s", appointment_id)
    try:
        async with async_session_factory() as db:
            appt_result = await db.execute(
                select(Appointment).where(Appointment.id == uuid.UUID(appointment_id))
            )
            appointment = appt_result.scalar_one_or_none()
            if not appointment:
                logger.warning("Reminder lifecycle job: appointment %s not found", appointment_id)
                return

            contractor_result = await db.execute(
                select(Contractor).where(Contractor.id == appointment.tenant_id)
            )
            contractor = contractor_result.scalar_one_or_none()
            if not contractor:
                logger.warning("Reminder lifecycle job: contractor not found | appt=%s", appointment_id)
                return

            svc = AppointmentLifecycleService()
            await svc.send_reminder(appointment, contractor, db)
            await db.commit()
    except Exception as exc:
        logger.error("Appointment reminder lifecycle job failed | appt=%s err=%s", appointment_id, exc)


# ---------------------------------------------------------------------------
# Job: daily call quality digest (fires 08:00 UTC)
# ---------------------------------------------------------------------------

async def schedule_nightly_aggregation() -> None:
    """
    Phase 5: Manually trigger nightly aggregation (e.g. for testing or backfill).
    Normally called by the 07:00 UTC cron job.
    """
    from datetime import date as _date
    stat_date = _date.today()
    logger.info("schedule_nightly_aggregation: manually triggered for %s", stat_date)
    await _nightly_aggregation_job(stat_date=stat_date.isoformat())


async def _nightly_aggregation_job(stat_date: str | None = None) -> None:
    """Phase 5: Aggregate daily_call_stats for all active tenants."""
    from app.database import async_session_factory
    from app.services.stats_aggregator import StatsAggregator
    from datetime import date as _date
    import datetime as _dt

    if stat_date:
        try:
            target_date = _date.fromisoformat(stat_date)
        except ValueError:
            target_date = _date.today()
    else:
        # Default: yesterday (since nightly runs at 07:00 UTC)
        target_date = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=1)).date()

    logger.info("Phase 5: Nightly aggregation for %s", target_date)
    try:
        async with async_session_factory() as db:
            aggregator = StatsAggregator()
            counts = await aggregator.aggregate_all_tenants(target_date, db)
            logger.info("Phase 5: Nightly aggregation complete | ok=%d error=%d", counts["ok"], counts["error"])
    except Exception as exc:
        logger.error("Phase 5: Nightly aggregation job failed: %s", exc)


async def _monthly_summary_email_job() -> None:
    """Phase 5: Send monthly summary emails to all active tenants (first of month)."""
    from app.database import async_session_factory
    from app.models.contractor import Contractor
    from app.services.monthly_summary_email import send_monthly_summary
    from sqlalchemy import select
    import datetime as _dt

    now = _dt.datetime.now(_dt.timezone.utc)
    # Send summary for the previous month
    if now.month == 1:
        year, month = now.year - 1, 12
    else:
        year, month = now.year, now.month - 1

    logger.info("Phase 5: Monthly summary email job | period=%d-%02d", year, month)
    try:
        async with async_session_factory() as db:
            result = await db.execute(
                select(Contractor).where(Contractor.is_active == True)  # noqa: E712
            )
            contractors = result.scalars().all()
            for contractor in contractors:
                try:
                    await send_monthly_summary(contractor, year, month, db)
                except Exception as exc:
                    logger.warning("Monthly summary email failed | tenant=%s err=%s", contractor.id, exc)
    except Exception as exc:
        logger.error("Phase 5: Monthly summary email job failed: %s", exc)


async def poll_weather_alerts_job() -> None:
    """
    Phase 6: Poll weather alerts for all contractors with weather_surge_mode flag enabled
    and service_area_postal_codes configured. Also runs check_and_expire().
    Public entry point for manual triggering / testing.
    """
    await _weather_surge_poll_and_expire_job()


async def _weather_surge_poll_and_expire_job() -> None:
    """
    Phase 6: Weather surge scheduler job — runs every 30 minutes.
    1. For each contractor with weather_surge_mode enabled and postal codes set: poll alerts.
    2. Activate surge for new triggering alerts.
    3. check_and_expire() all tenants.

    API failure → log, continue, never crash or leave tenant stuck in surge.
    """
    from app.database import async_session_factory
    from app.models.contractor import Contractor
    from app.models.weather_alert import WeatherAlert
    from app.services.feature_flags import is_enabled as _flag_enabled
    from app.services.weather_surge import WeatherSurgeService
    from sqlalchemy import select

    logger.info("Phase 6: Weather surge poll starting")
    svc = WeatherSurgeService()

    try:
        async with async_session_factory() as db:
            # Run expiry first
            expired = await svc.check_and_expire(db)
            logger.info("Phase 6: surge expiry check complete | expired=%d", expired)

            # Poll all active contractors
            result = await db.execute(
                select(Contractor).where(
                    Contractor.is_active == True,  # noqa: E712
                    Contractor.service_area_postal_codes.isnot(None),
                )
            )
            contractors = result.scalars().all()

            for contractor in contractors:
                try:
                    # Per-tenant flag check
                    flag_on = await _flag_enabled(
                        str(contractor.id), "weather_surge_mode", db
                    )
                    if not flag_on:
                        continue

                    alerts = await svc.poll_alerts(contractor, db)
                    for alert in alerts:
                        # Check if already processed this alert
                        existing = await db.execute(
                            select(WeatherAlert).where(
                                WeatherAlert.alert_id == alert.alert_id
                            )
                        )
                        if existing.scalar_one_or_none() is not None:
                            continue  # Already know about this alert

                        # Activate surge
                        if not contractor.surge_mode_active:
                            await svc.activate_surge(contractor, alert, db)
                            await db.commit()
                            logger.info(
                                "Phase 6: surge activated via polling | tenant=%s alert=%s",
                                contractor.id, alert.alert_id,
                            )
                        else:
                            # Still log the alert even if already in surge
                            from app.models.weather_alert import WeatherAlert as _WA
                            import uuid as _uuid
                            wa = _WA(
                                id=_uuid.uuid4(),
                                tenant_id=contractor.id,
                                alert_id=alert.alert_id,
                                surge_type=alert.surge_type,
                                title=alert.title,
                                effective_at=alert.effective_at,
                                expires_at=alert.expires_at,
                                source=alert.source,
                                postal_codes=alert.postal_codes,
                                raw_payload=alert.raw_payload,
                            )
                            db.add(wa)
                            await db.flush()
                            await db.commit()

                except Exception as exc:
                    logger.error(
                        "Phase 6: surge poll failed for contractor | tenant=%s err=%s",
                        contractor.id, exc,
                    )
                    # Never crash the whole job over one tenant's failure

    except Exception as exc:
        logger.error("Phase 6: weather surge poll job failed | err=%s", exc)


async def _daily_digest_job() -> None:
    from app.services.quality import daily_digest
    logger.info("Firing daily quality digest job")
    try:
        await daily_digest()
    except Exception as exc:
        logger.error("Daily digest job failed: %s", exc)
