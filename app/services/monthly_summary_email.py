"""
Phase 5: Monthly summary email.
Every dollar figure carries "estimated" label — never strip it.
Gated behind owner_dashboard_v2 flag.
"""
from __future__ import annotations

import logging
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

_MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


async def send_monthly_summary(
    contractor,  # Contractor model instance
    year: int,
    month: int,
    db: AsyncSession,
) -> None:
    """
    Sends plain-text monthly summary email.
    Every revenue figure MUST carry "estimated" label.
    If no email provider configured, logs instead of crashing.
    Gated behind owner_dashboard_v2.
    """
    from app.config import settings
    from app.services.stats_aggregator import StatsAggregator

    if not getattr(settings, "owner_dashboard_v2", False):
        logger.debug("send_monthly_summary: owner_dashboard_v2 OFF — skipping | tenant=%s", contractor.id)
        return

    aggregator = StatsAggregator()
    summary = await aggregator.get_monthly_summary(contractor.id, year, month, db)

    month_name = _MONTH_NAMES[month - 1]
    calls_answered = summary["calls_answered"]
    jobs_booked = summary["jobs_booked"]
    missed_recovered = summary["missed_calls_recovered"]
    no_shows_prevented = summary["no_shows_prevented"]

    # Revenue always labeled as estimated — non-negotiable rule
    estimated_revenue_cents = summary["estimated_revenue_cents"]
    estimated_revenue_dollars = estimated_revenue_cents / 100
    currency = summary["currency"]
    # is_estimated always True from aggregator — propagate to email
    assert summary["is_estimated"], "Revenue is_estimated flag must be True — label propagation failure"

    subject = f"TradeFlow — Your {month_name} Summary"
    body = (
        f"Hi {contractor.name},\n\n"
        f"Here's your TradeFlow summary for {month_name} {year}:\n\n"
        f"TradeFlow answered {calls_answered} calls and booked {jobs_booked} appointments.\n"
        f"Estimated revenue captured: ${estimated_revenue_dollars:,.2f} {currency} "
        f"(estimated from your avg ticket settings)\n"
        f"Missed calls recovered: {missed_recovered}\n"
        f"No-shows prevented: {no_shows_prevented}\n\n"
        f"Revenue figures are estimated based on your average ticket settings "
        f"and may differ from actual invoiced amounts.\n\n"
        f"— The TradeFlow Team"
    )

    if not contractor.email:
        logger.info(
            "send_monthly_summary: no email on contractor — logging only | tenant=%s\n%s",
            contractor.id, body,
        )
        return

    # Attempt SMTP send via existing mechanism
    try:
        from app.services.notifications import _smtp_enabled, _send_email
        if not _smtp_enabled():
            logger.info(
                "send_monthly_summary: SMTP not configured — logging only | tenant=%s\n%s",
                contractor.id, body,
            )
            return

        import asyncio
        loop = asyncio.get_event_loop()
        ok = await loop.run_in_executor(
            None,
            _send_email,
            contractor.email,
            subject,
            f"<pre style='font-family:monospace'>{body}</pre>",
            body,
        )
        if ok:
            logger.info("send_monthly_summary: sent to %s | tenant=%s", contractor.email, contractor.id)
        else:
            logger.warning("send_monthly_summary: send failed | tenant=%s", contractor.id)
    except Exception as exc:
        # Never crash — log and continue
        logger.warning(
            "send_monthly_summary: exception (non-fatal) | tenant=%s err=%s\n%s",
            contractor.id, exc, body,
        )
