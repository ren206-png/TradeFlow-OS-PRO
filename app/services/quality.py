"""
Call quality monitoring — Phase 4.

score_call()     — called at end of every call; writes quality flags to Lead
daily_digest()   — called by scheduler at 8am UTC; logs summary + emails admin
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.models.call import CallSession
    from app.models.lead import Lead
    from app.models.contractor import Contractor

logger = logging.getLogger(__name__)

# Thresholds
SHORT_CALL_SECS = 30       # hang-up / wrong number
VERY_SHORT_SECS = 60       # caller probably confused
NEGATIVE_SENTIMENTS = {"negative", "frustrated", "angry", "very_negative"}
HIGH_REVENUE_THRESHOLD = 60  # revenue_score ≥ this is a high-value lead


async def score_call(
    call_session: "CallSession",
    lead: "Lead | None",
    duration_seconds: int,
    db: "AsyncSession",
) -> list[str]:
    """
    Analyse a just-completed call and write quality flags to the Lead.

    Returns the list of flags written.
    """
    flags: list[str] = []

    duration = duration_seconds or 0

    # --- Duration-based flags ---
    if duration < SHORT_CALL_SECS:
        flags.append("hang_up_early")
    elif duration < VERY_SHORT_SECS:
        flags.append("very_short_call")

    if lead is None:
        # No lead created — call ended before any data was captured
        flags.append("no_lead_captured")
        logger.info(
            "quality: no_lead_captured | call_id=%s duration=%ds",
            call_session.retell_call_id, duration,
        )
        return flags

    # --- Sentiment ---
    sentiment = (lead.customer_sentiment or lead.sentiment or "").lower()
    if sentiment in NEGATIVE_SENTIMENTS:
        flags.append("negative_sentiment")

    # --- Booking outcome ---
    if lead.appointment_status == "not_booked":
        flags.append("not_booked")
        if (lead.revenue_score or 0) >= HIGH_REVENUE_THRESHOLD:
            flags.append("high_value_not_booked")

    # --- Escalation ---
    if lead.human_transfer_required:
        flags.append("human_transfer")

    # --- Life safety ---
    if lead.life_safety_risk:
        flags.append("life_safety_risk")

    # --- Out of service area ---
    if lead.service_area_status == "out_of_area":
        flags.append("out_of_area")

    # Persist to Lead
    lead.call_quality_flags = flags
    lead.hang_up_early = duration < SHORT_CALL_SECS
    await db.flush()

    if flags:
        logger.info(
            "quality: flags=%s | call_id=%s lead=%s duration=%ds",
            flags, call_session.retell_call_id, lead.id, duration,
        )

    return flags


# ---------------------------------------------------------------------------
# Daily digest
# ---------------------------------------------------------------------------

async def daily_digest() -> None:
    """
    Runs at 8am UTC. Aggregates yesterday's call quality across all contractors
    and logs a summary. If SMTP is configured, also emails the admin.
    """
    from sqlalchemy import func, select
    from app.database import async_session_factory
    from app.models.call import CallSession
    from app.models.lead import Lead
    from app.models.contractor import Contractor

    yesterday_start = (
        datetime.now(tz=timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        - timedelta(days=1)
    )
    yesterday_end = yesterday_start + timedelta(days=1)

    async with async_session_factory() as db:
        # Fetch all calls from yesterday
        result = await db.execute(
            select(CallSession).where(
                CallSession.started_at >= yesterday_start,
                CallSession.started_at < yesterday_end,
                CallSession.status == "completed",
            )
        )
        sessions = result.scalars().all()

        if not sessions:
            logger.info("daily_digest: no completed calls yesterday — skipping")
            return

        total = len(sessions)
        short_calls = sum(1 for s in sessions if (s.duration_seconds or 0) < SHORT_CALL_SECS)

        # Fetch linked leads
        lead_ids = [s.lead_id for s in sessions if s.lead_id]
        leads: list[Lead] = []
        if lead_ids:
            lead_result = await db.execute(select(Lead).where(Lead.id.in_(lead_ids)))
            leads = lead_result.scalars().all()

        booked = sum(1 for l in leads if l.appointment_status == "booked")
        negative = sum(
            1 for l in leads
            if (l.customer_sentiment or "").lower() in NEGATIVE_SENTIMENTS
        )
        transfers = sum(1 for l in leads if l.human_transfer_required)
        life_safety = sum(1 for l in leads if l.life_safety_risk)
        high_value_missed = sum(
            1 for l in leads
            if l.appointment_status == "not_booked"
            and (l.revenue_score or 0) >= HIGH_REVENUE_THRESHOLD
        )

        booking_rate = round(booked / len(leads) * 100, 1) if leads else 0.0

        summary_lines = [
            f"TradeFlow OS — Daily Call Quality Digest ({yesterday_start.date()})",
            f"",
            f"  Total calls:          {total}",
            f"  Leads captured:       {len(leads)}",
            f"  Booked:               {booked} ({booking_rate}%)",
            f"  Short/hang-up (<30s): {short_calls}",
            f"  Negative sentiment:   {negative}",
            f"  Human transfers:      {transfers}",
            f"  Life safety flags:    {life_safety}",
            f"  High-value missed:    {high_value_missed}",
        ]

        # Per-contractor breakdown
        contractor_ids = list({str(s.contractor_id) for s in sessions})
        contractors: dict = {}
        if contractor_ids:
            c_result = await db.execute(
                select(Contractor).where(Contractor.id.in_(contractor_ids))
            )
            contractors = {str(c.id): c for c in c_result.scalars().all()}
            summary_lines.append("")
            summary_lines.append("  Per contractor:")
            for cid in contractor_ids:
                c_sessions = [s for s in sessions if str(s.contractor_id) == cid]
                c_leads = [l for l in leads if str(l.contractor_id) == cid]
                c_booked = sum(1 for l in c_leads if l.appointment_status == "booked")
                c_rate = round(c_booked / len(c_leads) * 100, 1) if c_leads else 0.0
                name = contractors.get(cid, type("x", (), {"name": cid[:8]})()).name
                summary_lines.append(
                    f"    {name[:30]:<30}  calls={len(c_sessions)}  "
                    f"leads={len(c_leads)}  booked={c_booked} ({c_rate}%)"
                )

        summary = "\n".join(summary_lines)
        logger.info("daily_digest:\n%s", summary)

        # Email admin if SMTP is configured
        await _email_digest(summary, yesterday_start.date())

        # Feature 4: send per-contractor daily digest emails
        await _send_contractor_digest_emails(
            sessions=sessions,
            leads=leads,
            contractors=contractors,
            report_date=yesterday_start.date(),
        )


async def _email_digest(body: str, report_date) -> None:
    """Send digest email to admin. Silent no-op if SMTP is not configured."""
    import smtplib
    from email.mime.text import MIMEText
    from app.config import settings

    if not all([settings.smtp_host, settings.smtp_user, settings.smtp_password]):
        logger.debug("daily_digest: SMTP not configured — email skipped")
        return

    admin_email = settings.smtp_user  # send to same account (admin inbox)
    subject = f"TradeFlow OS — Call Quality Digest {report_date}"

    msg = MIMEText(body, "plain")
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from
    msg["To"] = admin_email

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
            smtp.starttls()
            smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.sendmail(settings.smtp_from, [admin_email], msg.as_string())
        logger.info("daily_digest: email sent to %s", admin_email)
    except Exception as exc:
        logger.warning("daily_digest: email failed — %s", exc)


async def _send_contractor_digest_emails(
    sessions: list,
    leads: list,
    contractors: dict,
    report_date,
) -> None:
    """Send per-contractor daily digest emails to active contractors with an email address."""
    from app.services.notifications import send_daily_digest_email

    MISSED_CALL_SECS = 10

    for cid, contractor in contractors.items():
        if not contractor.is_active or not contractor.email:
            continue

        c_sessions = [s for s in sessions if str(s.contractor_id) == cid]
        if not c_sessions:
            continue

        c_leads = [l for l in leads if str(l.contractor_id) == cid]
        c_booked = sum(1 for l in c_leads if l.appointment_status == "booked")
        c_booking_rate = round(c_booked / len(c_leads) * 100, 1) if c_leads else 0.0
        c_missed = sum(
            1 for s in c_sessions if (s.duration_seconds or 0) < MISSED_CALL_SECS
        )
        c_high_value_missed = sum(
            1 for l in c_leads
            if l.appointment_status == "not_booked"
            and (l.revenue_score or 0) >= HIGH_REVENUE_THRESHOLD
        )
        durations = [s.duration_seconds for s in c_sessions if s.duration_seconds]
        c_avg_duration = int(sum(durations) / len(durations)) if durations else 0
        c_emergency = sum(
            1 for l in c_leads
            if (l.emergency_level or "").lower() in ("emergency", "urgent")
            or l.life_safety_risk
        )

        stats = {
            "total_calls": len(c_sessions),
            "leads": len(c_leads),
            "booked": c_booked,
            "booking_rate": c_booking_rate,
            "missed_calls": c_missed,
            "high_value_missed": c_high_value_missed,
            "avg_duration_secs": c_avg_duration,
            "emergency_calls": c_emergency,
        }

        try:
            await send_daily_digest_email(contractor, stats, report_date)
            logger.info(
                "daily_digest: sent contractor digest to %s (%s)",
                contractor.email, contractor.name,
            )
        except Exception as exc:
            logger.error(
                "daily_digest: failed to send to contractor %s: %s", cid, exc
            )
