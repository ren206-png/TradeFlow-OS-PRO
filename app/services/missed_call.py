"""
Missed call recovery — sends an SMS to callers when the AI couldn't answer.
Called from the Retell webhook handler when call_status == "error" or duration < 5 seconds.
"""
from __future__ import annotations
import logging
from app.config import settings
logger = logging.getLogger(__name__)

async def send_missed_call_sms(to_number: str, contractor_name: str, ai_number: str) -> dict:
    """Send an SMS to a missed caller."""
    if not settings.twilio_account_sid or not settings.twilio_auth_token or not settings.twilio_from_number:
        logger.info("Twilio not configured — skipping missed call SMS to %s", to_number)
        return {"success": False, "error": "Twilio not configured"}

    try:
        from twilio.rest import Client
        client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
        body = (
            f"Hi! You recently called {contractor_name}. "
            f"Sorry we missed you — our AI assistant is available 24/7 at {ai_number}. "
            f"Please call back and we'll get you taken care of right away!"
        )
        message = client.messages.create(
            body=body,
            from_=settings.twilio_from_number,
            to=to_number,
        )
        logger.info("Missed call SMS sent | to=%s sid=%s", to_number, message.sid)
        return {"success": True, "sid": message.sid}
    except Exception as exc:
        logger.error("Missed call SMS failed | to=%s error=%s", to_number, exc)
        return {"success": False, "error": str(exc)}
