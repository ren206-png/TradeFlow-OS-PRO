"""
Phase 5: Nightly stats aggregation service.
Reads from raw tables ONLY during the nightly job.
Dashboard reads ONLY from daily_call_stats — never raw tables on page load.

Revenue figures always carry is_estimated=True — this label MUST NOT be stripped.
"""
from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.daily_call_stats import DailyCallStats

logger = logging.getLogger(__name__)


class StatsAggregator:

    async def aggregate_day(
        self, tenant_id: uuid.UUID, stat_date: date, db: AsyncSession
    ) -> DailyCallStats:
        """
        Compute stats for one tenant for one day from raw tables.
        Upserts into daily_call_stats (update if row exists for tenant+date).
        Revenue summed from revenue_attribution_ledger — always is_estimated=True.
        """
        from app.models.call import CallSession
        from app.models.lead import Lead
        from app.models.appointment import Appointment
        from app.models.revenue_attribution_ledger import RevenueAttributionLedger

        day_start = datetime(stat_date.year, stat_date.month, stat_date.day, 0, 0, 0, tzinfo=timezone.utc)
        day_end = datetime(stat_date.year, stat_date.month, stat_date.day, 23, 59, 59, 999999, tzinfo=timezone.utc)

        # ---- Call sessions for this tenant + day ----
        cs_result = await db.execute(
            select(CallSession).where(
                CallSession.contractor_id == tenant_id,
                CallSession.started_at >= day_start,
                CallSession.started_at <= day_end,
            )
        )
        call_sessions = cs_result.scalars().all()

        calls_total = len(call_sessions)
        calls_answered = sum(1 for c in call_sessions if c.status in ("completed", "transferred"))
        calls_transferred = sum(1 for c in call_sessions if c.status == "transferred")
        # abandoned: ended quickly without reaching booked status
        calls_abandoned = sum(
            1 for c in call_sessions
            if c.duration_seconds is not None and c.duration_seconds < 10
        )
        durations = [c.duration_seconds for c in call_sessions if c.duration_seconds is not None]
        avg_duration_seconds = int(sum(durations) / len(durations)) if durations else 0

        # ---- Leads for this day ----
        leads_result = await db.execute(
            select(Lead).where(
                Lead.contractor_id == tenant_id,
                Lead.created_at >= day_start,
                Lead.created_at <= day_end,
            )
        )
        leads = leads_result.scalars().all()

        calls_booked = sum(1 for l in leads if l.appointment_status == "booked")
        # booking_rate_pct: integer percentage 0-100, never float
        booking_rate_pct = int((calls_booked / calls_total * 100)) if calls_total > 0 else 0

        # ---- Revenue from attribution ledger ----
        rev_result = await db.execute(
            select(func.sum(RevenueAttributionLedger.attributed_value_cents)).where(
                RevenueAttributionLedger.tenant_id == tenant_id,
                RevenueAttributionLedger.created_at >= day_start,
                RevenueAttributionLedger.created_at <= day_end,
                RevenueAttributionLedger.is_correction == False,  # noqa: E712
            )
        )
        estimated_revenue_cents = int(rev_result.scalar() or 0)

        # ---- Appointments (reminders, no-shows) ----
        appt_result = await db.execute(
            select(Appointment).where(
                Appointment.tenant_id == tenant_id,
                Appointment.created_at >= day_start,
                Appointment.created_at <= day_end,
            )
        )
        appointments = appt_result.scalars().all()
        reminders_sent = sum(1 for a in appointments if a.reminder_sent_at is not None)
        no_shows_prevented = sum(1 for a in appointments if a.status == "confirmed")

        # ---- Spam blocked ----
        from app.models.spam_block import SpamBlock
        spam_result = await db.execute(
            select(func.count(SpamBlock.id)).where(
                SpamBlock.tenant_id == tenant_id,
                SpamBlock.created_at >= day_start,
                SpamBlock.created_at <= day_end,
                SpamBlock.is_active == True,  # noqa: E712
            )
        )
        calls_spam_blocked = int(spam_result.scalar() or 0)

        # ---- Upsert ----
        existing_result = await db.execute(
            select(DailyCallStats).where(
                DailyCallStats.tenant_id == tenant_id,
                DailyCallStats.stat_date == stat_date,
            )
        )
        row = existing_result.scalar_one_or_none()

        if row is None:
            row = DailyCallStats(
                tenant_id=tenant_id,
                stat_date=stat_date,
            )
            db.add(row)

        # Always is_estimated=True — revenue label MUST NOT be stripped
        row.calls_total = calls_total
        row.calls_answered = calls_answered
        row.calls_booked = calls_booked
        row.calls_abandoned = calls_abandoned
        row.calls_transferred = calls_transferred
        row.calls_spam_blocked = calls_spam_blocked
        row.avg_duration_seconds = avg_duration_seconds
        row.booking_rate_pct = booking_rate_pct
        row.estimated_revenue_cents = estimated_revenue_cents
        row.is_estimated = True  # Always True — label is non-negotiable
        row.reminders_sent = reminders_sent
        row.no_shows_prevented = no_shows_prevented

        await db.flush()
        return row

    async def aggregate_all_tenants(self, stat_date: date, db: AsyncSession) -> dict:
        """Run aggregate_day for every active contractor. Called nightly by scheduler."""
        from app.models.contractor import Contractor

        result = await db.execute(
            select(Contractor).where(Contractor.is_active == True)  # noqa: E712
        )
        contractors = result.scalars().all()

        counts = {"ok": 0, "error": 0}
        for contractor in contractors:
            try:
                await self.aggregate_day(contractor.id, stat_date, db)
                counts["ok"] += 1
            except Exception as exc:
                logger.error(
                    "aggregate_day failed | tenant=%s date=%s err=%s",
                    contractor.id, stat_date, exc,
                )
                counts["error"] += 1

        try:
            await db.commit()
        except Exception as exc:
            logger.error("aggregate_all_tenants commit failed: %s", exc)

        return counts

    async def get_monthly_summary(
        self, tenant_id: uuid.UUID, year: int, month: int, db: AsyncSession
    ) -> dict[str, Any]:
        """
        Sum daily_call_stats for the month.
        All revenue figures carry is_estimated — never strip it.
        """
        from sqlalchemy import and_, extract

        result = await db.execute(
            select(DailyCallStats).where(
                DailyCallStats.tenant_id == tenant_id,
                extract("year", DailyCallStats.stat_date) == year,
                extract("month", DailyCallStats.stat_date) == month,
            )
        )
        rows = result.scalars().all()

        if not rows:
            return {
                "calls_answered": 0,
                "jobs_booked": 0,
                "estimated_revenue_cents": 0,
                "currency": "CAD",
                "is_estimated": True,  # Always present — non-negotiable
                "missed_calls_recovered": 0,
                "no_shows_prevented": 0,
                "booking_rate_pct": 0,
            }

        calls_answered = sum(r.calls_answered for r in rows)
        jobs_booked = sum(r.calls_booked for r in rows)
        estimated_revenue_cents = sum(r.estimated_revenue_cents for r in rows)
        missed_calls_recovered = sum(r.missed_calls_recovered for r in rows)
        no_shows_prevented = sum(r.no_shows_prevented for r in rows)

        calls_total = sum(r.calls_total for r in rows)
        booking_rate_pct = int((jobs_booked / calls_total * 100)) if calls_total > 0 else 0

        # Currency: use the most common, default CAD
        currencies = [r.currency for r in rows if r.currency]
        currency = max(set(currencies), key=currencies.count) if currencies else "CAD"

        return {
            "calls_answered": calls_answered,
            "jobs_booked": jobs_booked,
            "estimated_revenue_cents": estimated_revenue_cents,
            "currency": currency,
            "is_estimated": True,  # Always labeled — NEVER strip
            "missed_calls_recovered": missed_calls_recovered,
            "no_shows_prevented": no_shows_prevented,
            "booking_rate_pct": booking_rate_pct,
        }
