import logging

from twilio.rest import Client as TwilioClient

from app.config import settings

logger = logging.getLogger(__name__)


class SMSService:
    """Send SMS messages via Twilio using per-message-type templates."""

    _TEMPLATES = {
        "confirmation": (
            "✅ Booked! {company} is scheduled for {appointment_time} at {service_address}. "
            "Reply STOP to opt out."
        ),
        "reminder": (
            "📅 Reminder: {company} arrives {appointment_time} at {service_address}. "
            "Questions? Call us back."
        ),
        "enroute": (
            "🚗 Your {company} technician is on the way! ETA: {appointment_time}. "
            "Call us if anything changes."
        ),
        "missed_call": (
            "Hi, {company} tried to reach you about your service request. "
            "Is this still an emergency, or would you like to schedule? Reply or call us back."
        ),
        "review_request": (
            "Thanks for choosing {company}! "
            "If we helped today, we'd love your review: {review_link} 🌟"
        ),
    }

    def __init__(self, contractor) -> None:
        self.contractor = contractor
        self._client = TwilioClient(settings.twilio_account_sid, settings.twilio_auth_token)

    def build_message(self, message_type: str, **kwargs) -> str:
        """Render the SMS body from a template."""
        template = self._TEMPLATES.get(message_type)
        if not template:
            raise ValueError(f"Unknown message_type: {message_type}")

        return template.format(
            company=self.contractor.name,
            review_link=self.contractor.review_link or "",
            appointment_time=kwargs.get("appointment_time", ""),
            service_address=kwargs.get("service_address", ""),
            eta=kwargs.get("eta", ""),
        )

    async def send(self, to_number: str, message_type: str, **kwargs) -> dict:
        """Send an SMS and return the result dict."""
        body = self.build_message(message_type, **kwargs)
        try:
            message = self._client.messages.create(
                body=body,
                from_=settings.twilio_from_number,
                to=to_number,
            )
            logger.info(
                "SMS sent",
                extra={"message_sid": message.sid, "to": to_number, "type": message_type},
            )
            return {"success": True, "message_sid": message.sid}
        except Exception as exc:
            logger.error("SMS send failed: %s", exc)
            return {"success": False, "error": str(exc)}
