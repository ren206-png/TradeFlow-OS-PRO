"""
Inbound Twilio SMS webhook — handles STOP / UNSTOP / HELP keywords.
Phase 2: also handles CALL keyword for missed-call textback callback.
Configure this URL in your Twilio Messaging Service:
  https://tradesflowos.com/twilio/sms
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Form, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.sms_compliance import handle_inbound_keyword

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/twilio", tags=["twilio"])


@router.post("/sms")
async def inbound_sms(
    request: Request,
    From: str = Form(...),
    Body: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    """
    Handles inbound SMS from Twilio.
    - STOP / STOPALL / UNSUBSCRIBE → opt-out, reply confirmation
    - START / UNSTOP / YES         → opt back in, reply confirmation
    - HELP / INFO                  → send help message
    - Anything else                → log and ignore (no reply)
    """
    phone = From.strip()
    To_number = request.form  # accessed below after form parsing
    body = Body.strip()
    logger.info("Inbound SMS | from=%s body=%r", phone, body[:80])

    reply = await handle_inbound_keyword(phone, body, db)

    if reply:
        # Return TwiML response
        twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Message>{reply}</Message>
</Response>"""
        return Response(content=twiml, media_type="application/xml")

    # Phase 2: CALL keyword — missed-call textback callback request
    # Only fires when missed_call_textback flag is ON for the tenant
    if body.strip().upper() == "CALL":
        try:
            call_reply = await _handle_call_keyword(phone, request, db)
            if call_reply:
                twiml = (
                    '<?xml version="1.0" encoding="UTF-8"?>'
                    f"<Response><Message>{call_reply}</Message></Response>"
                )
                return Response(content=twiml, media_type="application/xml")
        except Exception as _exc:
            logger.warning("CALL keyword handler failed | from=%s err=%s", phone, _exc)

    # Phase 4: CONFIRM keyword — mark appointment confirmed
    if body.strip().upper() == "CONFIRM":
        try:
            await _handle_confirm_keyword(phone, request, db)
        except Exception as _exc:
            logger.warning("CONFIRM keyword handler failed | from=%s err=%s", phone, _exc)
        # Return empty TwiML (no reply SMS on confirm)
        return Response(
            content='<?xml version="1.0" encoding="UTF-8"?><Response></Response>',
            media_type="application/xml",
        )

    # Phase 4: RESCHEDULE keyword — trigger outbound call to offer new slots
    if body.strip().upper() == "RESCHEDULE":
        try:
            await _handle_reschedule_keyword(phone, request, db)
        except Exception as _exc:
            logger.warning("RESCHEDULE keyword handler failed | from=%s err=%s", phone, _exc)
        twiml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<Response><Message>We're arranging a call to find you a new time. "
            "We'll call you shortly!</Message></Response>"
        )
        return Response(content=twiml, media_type="application/xml")

    # No keyword matched — return empty TwiML (no reply)
    return Response(
        content='<?xml version="1.0" encoding="UTF-8"?><Response></Response>',
        media_type="application/xml",
    )


async def _resolve_tenant_from_to(request: Request, db: AsyncSession):
    """Resolve contractor from the Twilio 'To' number in form data."""
    from app.models.contractor import Contractor
    form_data = await request.form()
    to_number: str = str(form_data.get("To", "")).strip()
    if not to_number:
        return None, None
    from sqlalchemy import select as _select
    result = await db.execute(
        _select(Contractor).where(
            Contractor.phone_number == to_number,
            Contractor.is_active.is_(True),
        )
    )
    contractor = result.scalar_one_or_none()
    return contractor, to_number


async def _handle_confirm_keyword(
    caller_phone: str,
    request: Request,
    db: AsyncSession,
) -> None:
    """
    Phase 4: Handle CONFIRM keyword — mark appointment confirmed.
    Gated behind appointment_lifecycle feature flag per tenant.
    """
    from app.services.appointment_lifecycle import AppointmentLifecycleService
    contractor, _ = await _resolve_tenant_from_to(request, db)
    if not contractor:
        return
    svc = AppointmentLifecycleService()
    await svc.handle_confirm_keyword(caller_phone, str(contractor.id), db)
    await db.commit()


async def _handle_reschedule_keyword(
    caller_phone: str,
    request: Request,
    db: AsyncSession,
) -> None:
    """
    Phase 4: Handle RESCHEDULE keyword — trigger outbound call to offer new slots.
    Gated behind appointment_lifecycle feature flag per tenant.
    """
    from app.services.appointment_lifecycle import AppointmentLifecycleService
    contractor, _ = await _resolve_tenant_from_to(request, db)
    if not contractor:
        return
    svc = AppointmentLifecycleService()
    await svc.handle_reschedule_keyword(caller_phone, str(contractor.id), db)
    await db.commit()


async def _handle_call_keyword(
    caller_phone: str,
    request: Request,
    db: AsyncSession,
) -> str | None:
    """
    Handle incoming CALL keyword SMS — trigger outbound AI callback.
    Returns a TwiML message string, or None if call cannot be placed.

    Idempotency: if a CallbackRequest from the same phone to the same
    contractor exists within the last 10 minutes, skip.
    """
    from app.models.callback_request import CallbackRequest
    from app.models.contractor import Contractor
    from app.services.feature_flags import is_enabled
    from app.services.retell_client import RetellClient

    # Determine which contractor owns the Twilio To number
    form_data = await request.form()
    to_number: str = str(form_data.get("To", "")).strip()

    if not to_number:
        logger.warning("CALL keyword: no To number in form data")
        return None

    result = await db.execute(
        select(Contractor).where(
            Contractor.phone_number == to_number,
            Contractor.is_active.is_(True),
        )
    )
    contractor = result.scalar_one_or_none()
    if not contractor:
        logger.warning("CALL keyword: no contractor for To=%s", to_number)
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
            from_number=to_number,
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
