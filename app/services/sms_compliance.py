"""
SMS Compliance Service — A2P 10DLC / TCPA

Responsibilities:
  - Check opt-out status before every send
  - Record implied consent when a caller initiates contact
  - Handle STOP / UNSTOP / START / HELP inbound keywords
  - Track whether a compliance footer has been sent to a number
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.sms_opt_out import SmsConsent, SmsOptOut

logger = logging.getLogger(__name__)

STOP_KEYWORDS  = {"stop", "stopall", "unsubscribe", "cancel", "end", "quit"}
START_KEYWORDS = {"start", "unstop", "yes"}
HELP_KEYWORDS  = {"help", "info"}

HELP_REPLY = (
    "TradeFlow AI: For support call back at any time. "
    "Reply STOP to opt out of messages."
)
STOP_REPLY = (
    "You have been unsubscribed from all messages. "
    "Reply START to re-subscribe."
)
START_REPLY = (
    "You have been re-subscribed to messages. "
    "Reply STOP at any time to opt out."
)


async def is_opted_out(phone: str, db: AsyncSession) -> bool:
    """Returns True if the number has an active opt-out record."""
    result = await db.execute(
        select(SmsOptOut).where(SmsOptOut.phone == phone, SmsOptOut.is_opted_out.is_(True))
    )
    return result.scalar_one_or_none() is not None


async def record_opt_out(phone: str, db: AsyncSession) -> None:
    """Mark a number as opted out (STOP). Upserts the record."""
    result = await db.execute(select(SmsOptOut).where(SmsOptOut.phone == phone))
    record = result.scalar_one_or_none()
    if record:
        record.is_opted_out = True
        record.opted_out_at = datetime.now(tz=timezone.utc)
        record.opted_back_in_at = None
    else:
        db.add(SmsOptOut(phone=phone, is_opted_out=True))
    await db.flush()
    logger.info("SMS opt-out recorded | phone=%s", phone)


async def record_opt_in(phone: str, db: AsyncSession) -> None:
    """Re-subscribe a number (UNSTOP / START). Upserts the record."""
    result = await db.execute(select(SmsOptOut).where(SmsOptOut.phone == phone))
    record = result.scalar_one_or_none()
    if record:
        record.is_opted_out = False
        record.opted_back_in_at = datetime.now(tz=timezone.utc)
        await db.flush()
    logger.info("SMS opt-in recorded | phone=%s", phone)


async def record_consent(phone: str, call_id: str, db: AsyncSession) -> None:
    """
    Record implied consent for a phone number based on inbound caller contact.
    Idempotent — does nothing if consent already exists for this call_id.
    """
    existing = await db.execute(
        select(SmsConsent).where(SmsConsent.source_call_id == call_id)
    )
    if existing.scalar_one_or_none():
        return  # already recorded
    db.add(SmsConsent(
        phone=phone,
        source_call_id=call_id,
        consent_basis="inbound_caller",
    ))
    await db.flush()
    logger.info("SMS consent recorded | phone=%s call_id=%s", phone, call_id)


async def needs_compliance_footer(phone: str, db: AsyncSession) -> bool:
    """
    Returns True if this is the first message to this number
    (compliance footer required on first contact).
    """
    result = await db.execute(
        select(SmsConsent).where(SmsConsent.phone == phone, SmsConsent.first_sms_sent.is_(True))
    )
    return result.scalar_one_or_none() is None


async def mark_first_sms_sent(phone: str, db: AsyncSession) -> None:
    """Mark that the first SMS (with compliance footer) has been sent."""
    result = await db.execute(select(SmsConsent).where(SmsConsent.phone == phone))
    record = result.scalar_one_or_none()
    if record:
        record.first_sms_sent = True
        await db.flush()


async def handle_inbound_keyword(
    phone: str, body: str, db: AsyncSession
) -> str | None:
    """
    Check if an inbound SMS is a STOP/START/HELP keyword.
    Returns a reply body string if a keyword was matched, else None.
    Records opt-out/in in DB.
    """
    keyword = body.strip().lower()
    if keyword in STOP_KEYWORDS:
        await record_opt_out(phone, db)
        await db.commit()
        return STOP_REPLY
    if keyword in START_KEYWORDS:
        await record_opt_in(phone, db)
        await db.commit()
        return START_REPLY
    if keyword in HELP_KEYWORDS:
        return HELP_REPLY
    return None
