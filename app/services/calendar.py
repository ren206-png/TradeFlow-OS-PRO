from __future__ import annotations
import logging

from app.models.contractor import Contractor
from app.tools.check_availability import _generate_slots

logger = logging.getLogger(__name__)


class CalendarService:
    """
    MVP calendar service — generates realistic slots based on urgency.
    Production stubs for Google Calendar and Calendly are marked TODO.
    """

    def __init__(self, contractor: Contractor) -> None:
        self.contractor = contractor

    async def get_available_slots(self, urgency: str, date_preference: str | None = None) -> list[dict]:
        """Return available slots. MVP delegates to the shared slot generator."""
        if self.contractor.calendar_provider == "google":
            # TODO: fetch from Google Calendar API using self.contractor.calendar_config
            logger.debug("Google Calendar stub — returning generated slots")
        elif self.contractor.calendar_provider == "calendly":
            # TODO: fetch from Calendly API using self.contractor.calendar_config
            logger.debug("Calendly stub — returning generated slots")

        return _generate_slots(urgency)

    async def book_slot(self, slot_id: str, details: dict) -> dict:
        """
        MVP: records the booking in memory/DB only.
        Production: write to the calendar provider via API.
        """
        if self.contractor.calendar_provider == "google":
            # TODO: create Google Calendar event
            pass
        elif self.contractor.calendar_provider == "calendly":
            # TODO: create Calendly booking
            pass

        return {
            "success": True,
            "calendar_event_id": f"manual-{slot_id}",
            "provider": self.contractor.calendar_provider,
        }
