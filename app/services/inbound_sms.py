"""
Carrier-agnostic inbound SMS handling: STOP / START / HELP, CALL (AI callback),
CONFIRM / RESCHEDULE (appointments). Called by the Telnyx webhook.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.sms_compliance import handle_inbound_keyword

logger = logging.getLogger(__name__)


async def handle_inbound_sms(phone: str, body: str, to_number: str, db: AsyncSession) -> str | None:
    """
    Carrier-agnostic inbound SMS handling. Returns the reply text, or None for no reply.
    - STOP / START / HELP       → compliance keywords
    - CALL                      → AI callback (missed_call_textback)
    - CONFIRM / RESCHEDULE      → appointment lifecycle
    """
    phone = phone.strip()
    body = body.strip()
    logger.info("Inbound SMS | from=%s to=%s body=%r", phone, to_number, body[:80])

    reply = await handle_inbound_keyword(phone, body, db)
    if reply:
        return reply

    keyword = body.upper()
    if keyword == "CALL":
        try:
            return await _handle_call_keyword(phone, to_number, db)
        except Exception as _exc:
            logger.warning("CALL keyword handler failed | from=%s err=%s", phone, _exc)
            return None

    if keyword == "CONFIRM":
        try:
            await _handle_confirm_keyword(phone, to_number, db)
        except Exception as _exc:
            logger.warning("CONFIRM keyword handler failed | from=%s err=%s", phone, _exc)
        return None

    if keyword == "RESCHEDULE":
        try:
            await _handle_reschedule_keyword(phone, to_number, db)
        except Exception as _exc:
            logger.warning("RESCHEDULE keyword handler failed | from=%s err=%s", phone, _exc)
        return "We're arranging a call to find you a new time. We'll call you shortly!"

    if body:
        try:
            await _forward_reply_to_owner(phone, body, to_number, db)
        except Exception as _exc:
            logger.warning("Forwarding customer reply failed | from=%s err=%s", phone, _exc)
    return None


_REPLY_FORWARDS: dict[str, list[float]] = {}
_REPLY_FORWARD_LIMIT = 5  # per sender per hour, so a stuck auto-responder can't flood the owner


async def _forward_reply_to_owner(phone: str, body: str, to_number: str, db: AsyncSession) -> None:
    """A customer answered a confirmation or follow-up text with free text. Pass it to the owner: before this,
    anything that wasn't a keyword was silently dropped and the owner never saw 'can you come at 2 instead?'."""
    import re
    import time
    from app.models.lead import Lead
    from app.services.notifications import owner_alert_phone
    from app.services.sms_provider import send_sms

    contractor, _ = await _resolve_tenant_from_to(to_number, db, phone)
    if contractor is None:
        return
    owner = owner_alert_phone(contractor)
    if not owner or re.sub(r"\D", "", owner)[-10:] == re.sub(r"\D", "", phone)[-10:]:
        return  # unknown tenant, no owner number, or the owner texting the line themselves

    now = time.time()
    recent = [t for t in _REPLY_FORWARDS.get(phone, []) if now - t < 3600]
    if len(recent) >= _REPLY_FORWARD_LIMIT:
        return
    _REPLY_FORWARDS[phone] = recent + [now]

    name = (await db.execute(
        select(Lead.caller_name).where(Lead.contractor_id == contractor.id, Lead.phone == phone)
        .order_by(Lead.created_at.desc()).limit(1)
    )).scalar_one_or_none()
    who = f"{name} ({phone})" if name else phone
    await send_sms(owner, f"Customer reply from {who}:\n\"{body[:300]}\"\nText or call them back at {phone}.", "customer_reply")
    logger.info("Customer reply forwarded to owner | contractor=%s from=%s", contractor.name, phone)


async def _resolve_tenant_from_to(to_number: str, db: AsyncSession, caller_phone: str = ""):
    """
    Resolve the contractor an inbound SMS belongs to.
    1. Contractor whose own number received it.
    2. Outbound texts come from a shared sender number, so fall back to the contractor
       that most recently texted this caller, then the most recent lead from this caller.
    """
    from app.models.contractor import Contractor
    from app.models.lead import Lead
    from app.models.outbound_ledger import OutboundLedger
    import uuid as _uuid

    if to_number:
        result = await db.execute(
            select(Contractor).where(Contractor.phone_number == to_number, Contractor.is_active.is_(True))
        )
        contractor = result.scalar_one_or_none()
        if contractor:
            return contractor, to_number

    if not caller_phone:
        return None, to_number

    tenant_id = (await db.execute(
        select(OutboundLedger.tenant_id)
        .where(OutboundLedger.recipient_phone == caller_phone, OutboundLedger.channel == "sms",
               OutboundLedger.status == "sent")
        .order_by(OutboundLedger.created_at.desc()).limit(1)
    )).scalar_one_or_none()
    contractor_id = None
    if tenant_id:
        try:
            contractor_id = _uuid.UUID(tenant_id)
        except ValueError:
            contractor_id = None
    if contractor_id is None:
        contractor_id = (await db.execute(
            select(Lead.contractor_id).where(Lead.phone == caller_phone)
            .order_by(Lead.created_at.desc()).limit(1)
        )).scalar_one_or_none()
    if contractor_id is None:
        return None, to_number

    contractor = (await db.execute(
        select(Contractor).where(Contractor.id == contractor_id, Contractor.is_active.is_(True))
    )).scalar_one_or_none()
    return contractor, to_number


async def _handle_confirm_keyword(
    caller_phone: str,
    to_number: str,
    db: AsyncSession,
) -> None:
    """
    Phase 4: Handle CONFIRM keyword — mark appointment confirmed.
    Gated behind appointment_lifecycle feature flag per tenant.
    """
    from app.services.appointment_lifecycle import AppointmentLifecycleService
    contractor, _ = await _resolve_tenant_from_to(to_number, db, caller_phone)
    if not contractor:
        return
    svc = AppointmentLifecycleService()
    await svc.handle_confirm_keyword(caller_phone, str(contractor.id), db)
    await db.commit()


async def _handle_reschedule_keyword(
    caller_phone: str,
    to_number: str,
    db: AsyncSession,
) -> None:
    """
    Phase 4: Handle RESCHEDULE keyword — trigger outbound call to offer new slots.
    Gated behind appointment_lifecycle feature flag per tenant.
    """
    from app.services.appointment_lifecycle import AppointmentLifecycleService
    contractor, _ = await _resolve_tenant_from_to(to_number, db, caller_phone)
    if not contractor:
        return
    svc = AppointmentLifecycleService()
    await svc.handle_reschedule_keyword(caller_phone, str(contractor.id), db)
    await db.commit()


async def _handle_call_keyword(
    caller_phone: str,
    to_number: str,
    db: AsyncSession,
) -> str | None:
    """
    Handle incoming CALL keyword SMS — trigger outbound AI callback.
    Returns the reply text, or None if the call cannot be placed.

    Idempotency: if a CallbackRequest from the same phone to the same
    contractor exists within the last 10 minutes, skip.
    """
    from app.models.callback_request import CallbackRequest
    from app.models.contractor import Contractor
    from app.services.feature_flags import is_enabled
    from app.services.retell_client import RetellClient

    contractor = None
    if to_number:
        result = await db.execute(
            select(Contractor).where(
                Contractor.phone_number == to_number,
                Contractor.is_active.is_(True),
            )
        )
        contractor = result.scalar_one_or_none()
    if not contractor:
        contractor, _ = await _resolve_tenant_from_to("", db, caller_phone)
    if not contractor:
        logger.warning("CALL keyword: no contractor for to=%s from=%s", to_number, caller_phone)
        return None

    tenant_id = str(contractor.id)

    # Check feature flag
    if not await is_enabled(tenant_id, "missed_call_textback", db):
        logger.debug("CALL keyword: flag off for tenant=%s", tenant_id)
        return None

    # Idempotency: max one callback request per phone per contractor per 10 min
    ten_min_ago = datetime.now(tz=timezone.utc) - timedelta(minutes=10)
    existing_result = await db.execute(
        select(CallbackRequest).where(
            CallbackRequest.caller_phone == caller_phone,
            CallbackRequest.tenant_id == tenant_id,
            CallbackRequest.created_at >= ten_min_ago,
        )
    )
    existing = existing_result.scalar_one_or_none()
    if existing:
        logger.info(
            "CALL keyword: idempotency hit | phone=%s tenant=%s", caller_phone, tenant_id
        )
        return "We're already arranging your callback! Give us just a moment."

    # Record the callback request
    cb = CallbackRequest(
        tenant_id=tenant_id,
        caller_phone=caller_phone,
        source="call_keyword_sms",
        status="calling",
    )
    db.add(cb)
    await db.flush()

    # Initiate outbound AI call
    try:
        client = RetellClient()
        call_result = await client.create_phone_call(
            to_number=caller_phone,
            from_number=contractor.phone_number,
            override_agent_id=contractor.retell_agent_id,
            metadata={
                "tenant_id": tenant_id,
                "call_type": "call_keyword_callback",
                "callback_request_id": str(cb.id),
            },
        )
        outbound_call_id = call_result.get("call_id") or call_result.get("id")
        cb.outbound_call_id = outbound_call_id
        await db.flush()
        logger.info(
            "CALL keyword: outbound call initiated | phone=%s call_id=%s",
            caller_phone, outbound_call_id,
        )
        return f"Perfect! We're calling you right now at {caller_phone}."
    except Exception as exc:
        cb.status = "failed"
        await db.flush()
        logger.error("CALL keyword: outbound call failed | phone=%s err=%s", caller_phone, exc)
        return "Sorry, we couldn't place the call right now. Please try calling us directly."
