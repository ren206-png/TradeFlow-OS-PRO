from __future__ import annotations

import logging
from typing import Optional

from app.services.sms_provider import send_sms, send_sms_sync

logger = logging.getLogger(__name__)

COMPLIANCE_FOOTER = " Msg&data rates may apply. Reply STOP to opt out."


class SMSService:
    """
    Send SMS messages via the configured provider (see sms_provider.py).
    - Checks opt-out table before every send.
    - Appends compliance footer on first message to each number.
    """

    def __init__(self, contractor) -> None:
        self.contractor = contractor
        self._db = None  # injected by async callers that need compliance checks

    def with_db(self, db):
        """Attach a DB session for opt-out / consent checks."""
        self._db = db
        return self

    def _send(self, to: str, body: str, message_type: str) -> dict:
        return send_sms_sync(to, body, message_type)

    async def _send_async(self, to: str, body: str, message_type: str) -> dict:
        return await send_sms(to, body, message_type)

    # ------------------------------------------------------------------
    # Async compliance-aware send (use this from async routes/services)
    # ------------------------------------------------------------------

    async def _send_compliant(self, to: str, body: str, message_type: str) -> dict:
        """
        Async wrapper that:
        1. Checks opt-out table — blocks send if opted out
        2. Appends compliance footer on first message to this number
        3. Dispatches via the configured SMS provider
        """
        if self._db is not None:
            from app.services.sms_compliance import is_opted_out, needs_compliance_footer, mark_first_sms_sent
            if await is_opted_out(to, self._db):
                logger.info("SMS blocked — opted out | to=%s type=%s", to, message_type)
                return {"success": False, "error": "opted_out"}

            if await needs_compliance_footer(to, self._db):
                body = body.rstrip() + COMPLIANCE_FOOTER
                await mark_first_sms_sent(to, self._db)

        return await self._send_async(to, body, message_type)

    # ------------------------------------------------------------------
    # Template methods — use _send_compliant when DB is available
    # ------------------------------------------------------------------

    async def send_booking_confirmation_async(
        self, phone: str, name: str, trade: str,
        date_str: str, time_str: str, address: str,
    ) -> dict:
        body = (
            f"Hi {name}, your {trade} appointment is confirmed for "
            f"{date_str} at {time_str} — {address}. "
            f"Questions? Call us back."
        )
        return await self._send_compliant(phone, body, "booking_confirmation")

    async def send_appointment_reminder_async(
        self, phone: str, name: str, date_str: str, time_str: str
    ) -> dict:
        body = (
            f"Hi {name}, reminder: your appointment is tomorrow, "
            f"{date_str} at {time_str}."
        )
        return await self._send_compliant(phone, body, "appointment_reminder")

    async def send_missed_call_recovery_async(self, phone: str) -> dict:
        body = (
            "Hi, we tried to reach you about your service request. "
            "Is this still urgent? Call or text us back anytime."
        )
        return await self._send_compliant(phone, body, "missed_call")

    async def send_review_request_async(
        self, phone: str, name: str, review_link: str
    ) -> dict:
        body = (
            f"Hi {name}, thank you for choosing us! "
            f"We'd love your feedback: {review_link}"
        )
        return await self._send_compliant(phone, body, "review_request")

    async def send_followup_async(self, phone: str, name: str) -> dict:
        body = (
            f"Hi {name}, we wanted to check in — are you still looking for help "
            f"with your service request? We're ready when you are."
        )
        return await self._send_compliant(phone, body, "followup")

    # ------------------------------------------------------------------
    # Sync fallbacks (legacy — kept for backward compat with sync callers)
    # ------------------------------------------------------------------

    def send_booking_confirmation(
        self, phone: str, name: str, trade: str,
        date_str: str, time_str: str, address: str,
    ) -> dict:
        body = (
            f"Hi {name}, your {trade} appointment is confirmed for "
            f"{date_str} at {time_str} — {address}. "
            f"Questions? Call us back. Reply STOP to opt out."
        )
        return self._send(phone, body, "booking_confirmation")

    def send_appointment_reminder(
        self, phone: str, name: str, date_str: str, time_str: str
    ) -> dict:
        body = (
            f"Hi {name}, reminder: your appointment is tomorrow, "
            f"{date_str} at {time_str}. Reply STOP to opt out."
        )
        return self._send(phone, body, "appointment_reminder")

    def send_missed_call_recovery(self, phone: str) -> dict:
        body = (
            "Hi, we tried to reach you about your service request. "
            "Is this still urgent? Call or text us back. Reply STOP to opt out."
        )
        return self._send(phone, body, "missed_call")

    def send_review_request(
        self, phone: str, name: str, review_link: str
    ) -> dict:
        body = (
            f"Hi {name}, thank you for choosing us! "
            f"We'd love your feedback: {review_link} "
            f"Reply STOP to opt out."
        )
        return self._send(phone, body, "review_request")

    def send_followup(self, phone: str, name: str) -> dict:
        body = (
            f"Hi {name}, we wanted to check in — are you still looking for help "
            f"with your service request? We're ready when you are. "
            f"Reply STOP to opt out."
        )
        return self._send(phone, body, "followup")
