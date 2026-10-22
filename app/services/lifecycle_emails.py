"""
Lifecycle emails for contractors, sent through Resend (replaces the Mailchimp drip).

  number_ready   sent by provisioning (welcome.py)
  day1_no_calls  a day after the AI number is live and no call has reached it yet: how to forward
  first_call     the first real conversation the AI has: summary + lead
  week_one       day 7: real stats, or a nudge when nothing has happened
  trial_ending   3 days or less left on the trial (scheduler job in scheduler.py)
  trial_expired  the trial ended without a subscription
  (upgrade thank-you and payment-failed live in welcome.py / billing.py)

Each email is sent at most once per account: the keys are stored in contractors.lifecycle_sent.
Everything here is account/service mail about the customer's own TradeFlow account, so it needs no
marketing consent, and nothing is sent once the account is paid up (except the thank-you).
"""
from __future__ import annotations

import asyncio
import html
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.services.notifications import _send_email
from app.services.welcome import PORTAL_URL, _button, _wrap

logger = logging.getLogger(__name__)

SETUP_URL = "https://tradesflowos.com/portal/setup"
SUBSCRIBE_URL = "https://tradesflowos.com/portal/subscribe?plan=starter"
REAL_CALL_SECONDS = 15  # shorter than this is a hang-up, not a conversation

_PAID = ("active", "trialing")


def sent_keys(contractor) -> set[str]:
    return {k for k in (contractor.lifecycle_sent or "").split(",") if k}


def mark_sent(contractor, key: str) -> None:
    contractor.lifecycle_sent = ",".join(sorted(sent_keys(contractor) | {key}))


def is_paid(contractor) -> bool:
    return (contractor.subscription_status or "trial") in _PAID


# ── builders ──────────────────────────────────────────────────────────────────

def build_day1_no_calls_email(business_name: str, phone_number: str, call_handling: str = "missed") -> tuple[str, str, str]:
    from app.utils.phone import format_display
    name = html.escape(business_name or "there")
    shown = html.escape(format_display(phone_number))
    what = ("every call goes to the AI" if call_handling == "all" else "the AI picks up whenever you miss a call")
    subject = "Your AI receptionist hasn't taken a call yet"
    body = f"""<p>Hi {name},</p>
<p>Your AI number <strong>{shown}</strong> is live, but no call has reached it yet. The usual reason is that your business line isn't forwarding to it yet.</p>
<ol>
  <li><strong>Call {shown} from your own phone</strong> and talk to it for a minute. It takes 60 seconds and you hear exactly what your customers will.</li>
  <li><strong>Turn on call forwarding</strong> so {what}. The Setup page shows the exact code for your carrier, and you can test it from there.</li>
</ol>
{_button(SETUP_URL, "Set up forwarding")}
<p>Stuck on a carrier? Reply with the carrier name and we'll send the exact steps.</p>"""
    text = (f"Hi {business_name or 'there'},\n\nYour AI number {shown} is live but no call has reached it yet, usually because "
            f"your business line isn't forwarding to it.\n1. Call it from your own phone.\n2. Turn on forwarding so {what}: {SETUP_URL}\n"
            "Stuck? Reply with your carrier and we'll send the steps.\n— The TradeFlow team")
    return subject, _wrap("Let's get your first call", body), text


def build_first_call_email(business_name: str, caller: str, summary: str, booked: bool) -> tuple[str, str, str]:
    name = html.escape(business_name or "there")
    who = html.escape(caller or "A caller")
    what = html.escape((summary or "").strip()[:400]) or "They spoke with your AI receptionist."
    outcome = "The appointment is booked." if booked else "Their details are saved in your portal so you can call back."
    subject = "Your AI receptionist just took its first call"
    body = f"""<p>Hi {name},</p>
<p>It's working. <strong>{who}</strong> just called and your AI handled it.</p>
<p style="background:#f3f4f6;border-radius:8px;padding:12px 14px">{what}</p>
<p>{outcome}</p>
{_button(PORTAL_URL, "See the call and lead")}"""
    text = (f"Hi {business_name or 'there'},\n\nYour AI receptionist just took its first call from {caller or 'a caller'}.\n"
            f"{(summary or '').strip()[:400]}\n{outcome}\nSee it: {PORTAL_URL}\n— The TradeFlow team")
    return subject, _wrap("Your first call 🎉", body), text


def build_week_one_email(business_name: str, calls: int, leads: int, booked: int, days_left: int | None) -> tuple[str, str, str]:
    name = html.escape(business_name or "there")
    if calls:
        subject = f"Your first week: {calls} call{'s' if calls != 1 else ''} answered"
        stats = (f"<p>In your first week your AI answered <strong>{calls}</strong> call{'s' if calls != 1 else ''}, "
                 f"captured <strong>{leads}</strong> lead{'s' if leads != 1 else ''} and booked <strong>{booked}</strong> job{'s' if booked != 1 else ''}.</p>")
        text_stats = f"In your first week your AI answered {calls} calls, captured {leads} leads and booked {booked} jobs."
        button = _button(PORTAL_URL, "Open my portal")
    else:
        subject = "Your AI receptionist is waiting for its first call"
        stats = ("<p>It's been a week and your AI hasn't answered a call yet. That almost always means forwarding isn't on. "
                 "It takes about two minutes and we'll help if you get stuck.</p>")
        text_stats = f"A week in and no calls yet, usually because forwarding isn't on: {SETUP_URL}"
        button = _button(SETUP_URL, "Set up forwarding")
    trial = ""
    if days_left is not None:
        trial = f"<p>Your free trial has {days_left} day{'s' if days_left != 1 else ''} left. No card is needed until you choose a plan.</p>"
    body = f"<p>Hi {name},</p>{stats}{trial}{button}"
    text = f"Hi {business_name or 'there'},\n\n{text_stats}\n— The TradeFlow team"
    return subject, _wrap("How your first week went", body), text


def build_trial_expired_email(business_name: str) -> tuple[str, str, str]:
    name = html.escape(business_name or "there")
    subject = "Your TradeFlow trial has ended: your AI receptionist is paused"
    body = f"""<p>Hi {name},</p>
<p>Your 14-day free trial has ended, so your AI receptionist has stopped answering calls. Your settings, leads and AI number are all saved.</p>
<p>Starter is <strong>$5.99/month</strong> and Pro is $97/month. Subscribe and it picks up again right away.</p>
{_button(SUBSCRIBE_URL, "Turn my AI receptionist back on")}"""
    text = (f"Hi {business_name or 'there'},\n\nYour 14-day free trial has ended, so your AI receptionist has stopped answering calls. "
            f"Your settings, leads and number are saved.\nStarter is $5.99/month, Pro is $97/month: {SUBSCRIBE_URL}\n— The TradeFlow team")
    return subject, _wrap("Your trial has ended", body), text


def build_payment_failed_email(business_name: str) -> tuple[str, str, str]:
    name = html.escape(business_name or "there")
    url = "https://tradesflowos.com/portal/settings"
    subject = "Your TradeFlow payment didn't go through"
    body = f"""<p>Hi {name},</p>
<p>We couldn't charge your card for your TradeFlow subscription. Please update your payment details so your AI receptionist keeps answering calls.</p>
{_button(url, "Update my card")}"""
    text = (f"Hi {business_name or 'there'},\n\nWe couldn't charge your card for your TradeFlow subscription. "
            f"Please update your payment details: {url}\n— The TradeFlow team")
    return subject, _wrap("Payment didn't go through", body), text


# ── senders ───────────────────────────────────────────────────────────────────

async def _send(email: str, built: tuple[str, str, str], label: str) -> bool:
    if not email:
        return False
    subject, html_body, text = built
    ok = await asyncio.to_thread(_send_email, email, subject, html_body, text)
    logger.info("lifecycle email %s %s | to=%s", label, "sent" if ok else "not sent", email)
    return ok


async def send_payment_failed_email(email: str, business_name: str) -> bool:
    return await _send(email, build_payment_failed_email(business_name), "payment_failed")


async def maybe_send_first_call_email(contractor_id: str, call_id: str) -> bool:
    """Called after a call ends. Sends once, for the first conversation that lasted long enough to matter."""
    import uuid
    from app.database import async_session_factory
    from app.models.call import CallSession
    from app.models.contractor import Contractor
    from app.models.lead import Lead

    try:
        async with async_session_factory() as db:
            c = (await db.execute(select(Contractor).where(Contractor.id == uuid.UUID(str(contractor_id))))).scalar_one_or_none()
            if c is None or not c.email or "first_call" in sent_keys(c):
                return False
            session = (await db.execute(select(CallSession).where(CallSession.retell_call_id == call_id))).scalar_one_or_none()
            if session is None or (session.duration_seconds or 0) < REAL_CALL_SECONDS:
                return False
            lead = await db.get(Lead, session.lead_id) if session.lead_id else None
            booked = bool(lead and (lead.appointment_status or "") not in ("", "not_booked"))
            built = build_first_call_email(
                c.name, getattr(lead, "caller_name", "") or "", getattr(lead, "ai_summary", "") or getattr(lead, "problem_summary", "") or "", booked)
            mark_sent(c, "first_call")  # claim first so two overlapping webhooks can't both send
            await db.commit()
            ok = await _send(c.email, built, "first_call")
            if not ok:
                c.lifecycle_sent = ",".join(sorted(sent_keys(c) - {"first_call"}))
                await db.commit()
            return ok
    except Exception as exc:
        logger.error("first-call email failed | contractor=%s err=%s", contractor_id, exc)
        return False


async def _call_stats(db, contractor_id) -> tuple[int, int, int]:
    from app.models.call import CallSession
    from app.models.lead import Lead
    calls = (await db.execute(select(func.count()).select_from(CallSession).where(
        CallSession.contractor_id == contractor_id, CallSession.duration_seconds >= REAL_CALL_SECONDS))).scalar_one()
    leads = (await db.execute(select(func.count()).select_from(Lead).where(Lead.contractor_id == contractor_id))).scalar_one()
    booked = (await db.execute(select(func.count()).select_from(Lead).where(
        Lead.contractor_id == contractor_id, Lead.appointment_status.notin_(("not_booked", ""))))).scalar_one()
    return calls, leads, booked


def _aware(dt):
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def run_lifecycle_emails(db, now: datetime | None = None) -> dict[str, int]:
    """One pass of the daily job. Returns how many of each email went out."""
    from app.models.contractor import Contractor
    from app.services.billing import trial_days_left
    from app.services.welcome import send_trial_ending_email

    now = now or datetime.now(tz=timezone.utc)
    sent = {"day1_no_calls": 0, "week_one": 0, "trial_ending": 0, "trial_expired": 0}
    rows = (await db.execute(select(Contractor).where(
        Contractor.email.isnot(None), Contractor.email_verified_at.isnot(None),
        Contractor.provisioning_status == "active", Contractor.provisioned_at.isnot(None),
        Contractor.is_active.is_(True),
    ))).scalars().all()
    for c in rows:
        try:
            keys = sent_keys(c)
            live_for = now - _aware(c.provisioned_at)
            if is_paid(c) or (c.subscription_status or "trial") in ("cancelled", "canceled", "past_due"):
                continue

            ends = _aware(c.trial_ends_at)
            if ends and ends <= now:
                if "trial_expired" not in keys and now - ends < timedelta(days=7):
                    if await _send(c.email, build_trial_expired_email(c.name), "trial_expired"):
                        mark_sent(c, "trial_expired"); sent["trial_expired"] += 1
                continue

            days = trial_days_left(c)
            if days is not None and days <= 3 and "trial_ending" not in keys and c.trial_reminder_sent_at is None:
                if await send_trial_ending_email(c.email, c.name, days):
                    c.trial_reminder_sent_at = now; mark_sent(c, "trial_ending"); sent["trial_ending"] += 1

            calls, leads, booked = await _call_stats(db, c.id)
            if live_for >= timedelta(days=1) and live_for < timedelta(days=6) and calls == 0 and "day1_no_calls" not in keys:
                if await _send(c.email, build_day1_no_calls_email(c.name, c.phone_number or "", c.call_handling), "day1_no_calls"):
                    mark_sent(c, "day1_no_calls"); sent["day1_no_calls"] += 1
            if timedelta(days=7) <= live_for < timedelta(days=11) and "week_one" not in keys:
                if await _send(c.email, build_week_one_email(c.name, calls, leads, booked, days), "week_one"):
                    mark_sent(c, "week_one"); sent["week_one"] += 1
        except Exception as exc:
            logger.error("lifecycle pass failed for %s: %s", c.id, exc)
    await db.commit()
    return sent
