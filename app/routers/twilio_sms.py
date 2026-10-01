"""
Inbound SMS keyword handling (STOP / START / HELP / CALL / CONFIRM / RESCHEDULE)
plus the Twilio webhook. Telnyx inbound lives in telnyx_sms.py and reuses handle_inbound_sms.
Configure this URL in your Twilio Messaging Service:
  https://tradesflowos.com/twilio/sms
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Form, Header, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.services.sms_compliance import handle_inbound_keyword

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/twilio", tags=["twilio"])


async def _verify_twilio_signature(
    request: Request,
    x_twilio_signature: str = Header(default=""),
) -> None:
    """
    Verify the X-Twilio-Signature header to ensure the request is from Twilio.
    Returns 503 when TWILIO_AUTH_TOKEN is not configured.
    Raises HTTP 403 if the signature is invalid.
    """
    auth_token = settings.twilio_auth_token
    if not auth_token:
        # Fail closed: an unverifiable endpoint would let anyone fake STOP/CALL keywords.
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Twilio SMS not configured.")

    try:
        from twilio.request_validator import RequestValidator
        validator = RequestValidator(auth_token)

        # Reconstruct the full URL that Twilio signed
        url = str(request.url)

        # Form params must be passed as a dict for signature validation
        form_data = await request.form()
        params = dict(form_data)

        if not validator.validate(url, params, x_twilio_signature):
            logger.warning("twilio: invalid signature from %s", request.client)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid Twilio signature.",
            )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("twilio: signature validation error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Signature validation failed.",
        )


_EMPTY_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'


def _twiml(reply: str | None) -> Response:
    if not reply:
        return Response(content=_EMPTY_TWIML, media_type="application/xml")
    safe = reply.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return Response(
        content=f'<?xml version="1.0" encoding="UTF-8"?><Response><Message>{safe}</Message></Response>',
        media_type="application/xml",
    )


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

    return None


@router.post("/sms")
async def inbound_sms(
    request: Request,
    From: str = Form(...),
    Body: str = Form(...),
    To: str = Form(""),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_verify_twilio_signature),
):
    return _twiml(await handle_inbound_sms(From, Body, To.strip(), db))


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
