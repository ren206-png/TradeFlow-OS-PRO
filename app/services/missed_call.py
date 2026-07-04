"""
Missed call recovery SMS — sent when the AI couldn't answer.
Routes through Messaging Service SID when configured (A2P compliant).
Checks opt-out table before sending.
"""
from __future__ import annotations
import logging
from app.config import settings
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
