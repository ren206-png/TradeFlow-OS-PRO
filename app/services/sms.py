from __future__ import annotations

import logging
from typing import Optional

from app.config import settings

logger = logging.getLogger(__name__)


class SMSService:
    """Send SMS messages via Twilio using per-message-type template methods."""

    def __init__(self, contractor) -> None:
        self.contractor = contractor
        self._client = None

    def _get_client(self):
        if self._client is None:
            from twilio.rest import Client as TwilioClient
            self._client = TwilioClient(settings.twilio_account_sid, settings.twilio_auth_token)
        return self._client

    # ------------------------------------------------------------------
    # Internal helper
    # ------------------------------------------------------------------

    def _send(self, to: str, body: str, message_type: str) -> dict:
        """Dispatch a single SMS and return a result dict."""
        if not settings.twilio_account_sid or not settings.twilio_auth_token:
            logger.warning("Twilio not configured — SMS skipped [%s]", message_type)
            return {"success": False, "error": "Twilio not configured"}
        try:
            message = self._get_client().messages.create(
                body=body,
                from_=settings.twilio_from_number,
                to=to,
            )
            logger.info(
                "SMS sent",
                extra={"message_sid": message.sid, "to": to, "type": message_type},
            )
            return {"success": True, "sid": message.sid}
        except Exception as exc:
            logger.error("SMS send failed [%s]: %s", message_type, exc)
            return {"success": False, "error": str(exc)}

    # ------------------------------------------------------------------
    # Template methods
    # ------------------------------------------------------------------

    def send_booking_confirmation(
        self,
        phone: str,
        name: str,
        trade: str,
        date_str: str,
        time_str: str,
        address: str,
    ) -> dict:
        """Confirm a booked appointment with the customer."""
        body = (
            f"Hi {name}, your {trade} appointment is confirmed for "
            f"{date_str} at {time_str} — {address}. "
            f"Questions? Call us back. Reply STOP to opt out."
        )
        return self._send(phone, body, "booking_confirmation")

    def send_appointment_reminder(
        self,
        phone: str,
        name: str,
        date_str: str,
        time_str: str,
    ) -> dict:
        """Remind a customer of an upcoming appointment."""
        body = (
            f"Hi {name}, just a reminder: your appointment is tomorrow, "
            f"{date_str} at {time_str}. Reply STOP to opt out."
        )
        return self._send(phone, body, "appointment_reminder")

    def send_missed_call_recovery(self, phone: str) -> dict:
        """Follow up on a missed inbound call."""
        body = (
            "Hi, we tried to reach you about your service request. "
            "Is this still urgent, or would you like to schedule a time? "
            "Call or text us back anytime. Reply STOP to opt out."
        )
        return self._send(phone, body, "missed_call")

    def send_review_request(
        self,
        phone: str,
        name: str,
        review_link: str,
    ) -> dict:
        """Ask a satisfied customer to leave a review."""
        body = (
            f"Hi {name}, thank you for choosing us! "
            f"If we helped today, we'd love your feedback: {review_link} "
            f"Reply STOP to opt out."
        )
        return self._send(phone, body, "review_request")

    def send_followup(self, phone: str, name: str) -> dict:
        """Follow up with a lead that did not book."""
        body = (
            f"Hi {name}, we wanted to check in — are you still looking for help "
            f"with your service request? We're ready when you are. "
            f"Reply STOP to opt out."
        )
        return self._send(phone, body, "followup")
