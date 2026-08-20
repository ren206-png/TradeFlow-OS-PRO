"""
Missed call recovery SMS — sent when the AI couldn't answer.
Routes through Messaging Service SID when configured (A2P compliant).
Checks opt-out table before sending.

Phase 2: handle_missed_call() added — feature-flagged, uses OutboundGateway.
"""
from __future__ import annotations
import logging
from datetime import datetime, timedelta, timezone

from app.config import settings
from app.models.consent_ledger import ConsentLedger
logger = logging.getLogger(__name__)


async def send_missed_call_sms(
    to_number: str,
    contractor_name: str,
    ai_number: str,
    db=None,
) -> dict:
    """Send an SMS to a missed caller. Checks opt-out if db is provided."""
    if not settings.twilio_account_sid or not settings.twilio_auth_token:
        logger.info("Twilio not configured — skipping missed call SMS to %s", to_number)
        return {"success": False, "error": "Twilio not configured"}

    # Opt-out check
    if db is not None:
        from app.services.sms_compliance import is_opted_out, needs_compliance_footer, mark_first_sms_sent
        if await is_opted_out(to_number, db):
            logger.info("Missed call SMS blocked — opted out | to=%s", to_number)
            return {"success": False, "error": "opted_out"}

    try:
        from twilio.rest import Client
        client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
        body = (
            f"Hi! You recently called {contractor_name}. "
            f"Sorry we missed you — our AI assistant is available 24/7 at {ai_number}. "
            f"Call back anytime and we'll get you sorted right away!"
        )

        # Add compliance footer on first contact
        if db is not None:
            from app.services.sms_compliance import needs_compliance_footer, mark_first_sms_sent
            if await needs_compliance_footer(to_number, db):
                body += " Msg&data rates may apply. Reply STOP to opt out."
                await mark_first_sms_sent(to_number, db)

        params: dict = {"body": body, "to": to_number}
        if settings.twilio_messaging_service_sid:
            params["messaging_service_sid"] = settings.twilio_messaging_service_sid
        else:
            params["from_"] = settings.twilio_from_number

        message = client.messages.create(**params)
        logger.info("Missed call SMS sent | to=%s sid=%s", to_number, message.sid)
        return {"success": True, "sid": message.sid}
    except Exception as exc:
        logger.error("Missed call SMS failed | to=%s error=%s", to_number, exc)
        return {"success": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Phase 2: Feature-flagged missed-call textback via OutboundGateway
# ---------------------------------------------------------------------------

async def handle_missed_call(
    contractor,
    caller_phone: str,
    call_id: str,
    db,
) -> None:
    """
    Feature-flagged missed-call textback (Phase 2).

    1. Check missed_call_textback flag — if OFF, return immediately (no-op)
    2. Check opt-out for caller_phone
    3. Build booking URL
    4. Send via OutboundGateway.send() with idempotency_key=missed_call_{call_id}
    5. Write ConsentLedger entry (implied_inbound, 730-day CASL window)
    """
    from app.services.feature_flags import is_enabled

    tenant_id = str(contractor.id)

    # Step 1: Feature flag guard — OFF means current production behaviour exactly
    flag_on = await is_enabled(tenant_id, "missed_call_textback", db)
    if not flag_on:
        logger.debug(
            "handle_missed_call: flag off for tenant=%s, returning immediately", tenant_id
        )
        return

    # Step 2: Opt-out check
    try:
        from app.services.sms_compliance import is_opted_out
        if await is_opted_out(caller_phone, db):
            logger.info(
                "handle_missed_call: caller opted out | phone=%s tenant=%s",
                caller_phone, tenant_id,
            )
            return
    except Exception as exc:
        logger.warning("handle_missed_call: opt-out check failed | err=%s", exc)

    # Step 3: Build booking URL (prefer contractor.booking_url if it exists)
    booking_url: str = (
        getattr(contractor, "booking_url", None)
        or f"https://tradesflowos.com/book/{contractor.api_key}"
    )

    # Step 4: Send via OutboundGateway — ALL outbound goes through the gateway
    display_name = (
        getattr(contractor, "agent_name", None)
        or getattr(contractor, "name", "us")
    )
    message = (
        f"Hi, you just called {display_name}. "
        f"We didn't want to miss you — book online: {booking_url} "
        f"or reply CALL and we'll phone you right back."
    )

    try:
        from app.schemas.outbound import OutboundRequest
        from app.services.outbound_gateway import OutboundGateway

        req = OutboundRequest(
            tenant_id=tenant_id,
            recipient_phone=caller_phone,
            channel="sms",
            message=message,
            template_id="missed_call_textback",
            idempotency_key=f"missed_call_{call_id}",
        )
        gateway = OutboundGateway()
        result = await gateway.send(req, db)
        if result.success:
            logger.info(
                "handle_missed_call: SMS sent | call_id=%s phone=%s ledger=%s",
                call_id, caller_phone, result.ledger_id,
            )
        else:
            logger.info(
                "handle_missed_call: SMS blocked | call_id=%s reason=%s",
                call_id, result.block_reason,
            )
    except Exception as exc:
        logger.error(
            "handle_missed_call: gateway send failed | call_id=%s err=%s", call_id, exc
        )

    # Step 5: ConsentLedger entry — append-only, non-fatal
    try:
        expires = datetime.now(tz=timezone.utc) + timedelta(days=730)
        consent_row = ConsentLedger(
            tenant_id=tenant_id,
            recipient_phone=caller_phone,
            channel="sms",
            consent_type="implied_inbound",
            consent_basis="caller placed missed inbound call — implied CASL consent",
            evidence_call_id=call_id,
            expires_at=expires,
        )
        db.add(consent_row)
        await db.flush()
        logger.debug(
            "handle_missed_call: consent ledger written | call_id=%s phone=%s",
            call_id, caller_phone,
        )
    except Exception as exc:
        logger.warning(
            "handle_missed_call: consent ledger write failed (non-fatal) | err=%s", exc
        )
