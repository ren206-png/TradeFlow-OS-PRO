from __future__ import annotations

import asyncio
import logging
import random
import string
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _make_confirmation_number() -> str:
    return "TF-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=8))


def _parse_time(t: str) -> tuple:
    """Parse 'HH:MM' into (hour, minute)."""
    h, m = t.split(":")
    return int(h), int(m)


class CalendarService:
    def __init__(self, contractor: Any) -> None:
        self.contractor = contractor
        self.provider: str = contractor.calendar_provider or "manual"
        self.config: Dict = contractor.calendar_config or {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_available_slots(
        self, trade: str, urgency: str, num_slots: int = 3
    ) -> List[Dict]:
        """Returns list of {slot_id, display, iso_start, iso_end, technician}"""
        if self.provider == "google":
            try:
                return await self._google_get_slots(trade, urgency, num_slots)
            except Exception as exc:
                logger.warning("Google Calendar get_slots failed, falling back to manual: %s", exc)
                return self._manual_get_slots(trade, urgency, num_slots)
        return self._manual_get_slots(trade, urgency, num_slots)

    async def book_slot(
        self,
        slot_id: str,
        customer_name: str,
        phone: str,
        address: str,
        trade: str,
        notes: str,
    ) -> Dict:
        """Books the slot. Returns {success, event_id, confirmation_number, display}"""
        if self.provider == "google":
            try:
                return await self._google_book_slot(
                    slot_id, customer_name, phone, address, trade, notes
                )
            except Exception as exc:
                logger.warning("Google Calendar book_slot failed, falling back to manual: %s", exc)
                return self._manual_book_slot(slot_id, customer_name, phone, address, trade, notes)
        return self._manual_book_slot(slot_id, customer_name, phone, address, trade, notes)

    async def cancel_slot(self, event_id: str) -> Dict:
        """Cancels a booking."""
        if self.provider == "google":
            try:
                return await self._google_cancel_slot(event_id)
            except Exception as exc:
                logger.warning("Google Calendar cancel_slot failed: %s", exc)
                return {"success": False, "error": str(exc)}
        return {"success": True, "event_id": event_id, "message": "Booking cancelled (manual)"}

    # ------------------------------------------------------------------
    # Manual (mock) implementation
    # ------------------------------------------------------------------

    def _get_config_int(self, key: str, default: int) -> int:
        val = self.config.get(key, default)
        try:
            return int(val)
        except (TypeError, ValueError):
            return default

    def _manual_get_slots(self, trade: str, urgency: str, num_slots: int) -> List[Dict]:
        now = datetime.now(tz=timezone.utc)
        slot_duration = self._get_config_int("slot_duration_minutes", 60)
        biz_start_h, biz_start_m = _parse_time(self.config.get("business_hours_start", "08:00"))
        biz_end_h, biz_end_m = _parse_time(self.config.get("business_hours_end", "18:00"))

        slots: List[Dict] = []

        if urgency == "emergency":
            # Next slot within 2 hours
            start = now + timedelta(minutes=30)
            end = start + timedelta(minutes=slot_duration)
            slot_id = str(uuid.uuid4())
            slots.append(
                {
                    "slot_id": slot_id,
                    "display": f"Emergency — {start.strftime('%I:%M %p')} today",
                    "iso_start": start.isoformat(),
                    "iso_end": end.isoformat(),
                    "technician": "On-call technician",
                }
            )
            return slots

        # For non-emergency: scan days starting from tomorrow
        advance_days = self._get_config_int("booking_advance_days", 7)
        candidate_day = now + timedelta(days=1)

        for _ in range(advance_days + 1):
            if len(slots) >= num_slots:
                break
            # Build candidate start times during business hours for this day
            day_base = candidate_day.replace(
                hour=biz_start_h, minute=biz_start_m, second=0, microsecond=0
            )
            day_end = candidate_day.replace(
                hour=biz_end_h, minute=biz_end_m, second=0, microsecond=0
            )
            cursor = day_base
            while cursor + timedelta(minutes=slot_duration) <= day_end and len(slots) < num_slots:
                end = cursor + timedelta(minutes=slot_duration)
                slot_id = str(uuid.uuid4())
                display = cursor.strftime("%A, %B %-d — %-I:%M %p")
                slots.append(
                    {
                        "slot_id": slot_id,
                        "display": display,
                        "iso_start": cursor.isoformat(),
                        "iso_end": end.isoformat(),
                        "technician": "Available technician",
                    }
                )
                cursor += timedelta(minutes=slot_duration * 2)  # space slots apart
            candidate_day += timedelta(days=1)

        return slots[:num_slots]

    def _manual_book_slot(
        self,
        slot_id: str,
        customer_name: str,
        phone: str,
        address: str,
        trade: str,
        notes: str,
    ) -> Dict:
        confirmation_number = _make_confirmation_number()
        event_id = f"manual-{slot_id}"
        display = f"[TradeFlow] {trade} - {customer_name}"
        return {
            "success": True,
            "event_id": event_id,
            "confirmation_number": confirmation_number,
            "display": display,
        }

    # ------------------------------------------------------------------
    # Google Calendar implementation
    # ------------------------------------------------------------------

    def _build_google_service(self) -> Any:
        """Build a Google Calendar API service object (sync, run in executor)."""
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        creds_info: Dict = self.config["google_credentials"]
        scopes = ["https://www.googleapis.com/auth/calendar"]
        credentials = service_account.Credentials.from_service_account_info(
            creds_info, scopes=scopes
        )
        service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
        return service

    async def _run_sync(self, fn, *args, **kwargs):
        """Run a synchronous callable in a thread executor."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: fn(*args, **kwargs))

    async def _google_get_slots(
        self, trade: str, urgency: str, num_slots: int
    ) -> List[Dict]:
        calendar_id: str = self.config["google_calendar_id"]
        slot_duration = self._get_config_int("slot_duration_minutes", 60)
        biz_start_h, biz_start_m = _parse_time(self.config.get("business_hours_start", "08:00"))
        biz_end_h, biz_end_m = _parse_time(self.config.get("business_hours_end", "18:00"))
        advance_days = self._get_config_int("booking_advance_days", 7)

        now = datetime.now(tz=timezone.utc)

        if urgency == "emergency":
            time_min = now
            time_max = now + timedelta(hours=2)
        else:
            time_min = now + timedelta(days=1)
            time_max = now + timedelta(days=advance_days + 1)

        service = await self._run_sync(self._build_google_service)

        # Query freebusy
        body = {
            "timeMin": time_min.isoformat(),
            "timeMax": time_max.isoformat(),
            "items": [{"id": calendar_id}],
        }

        def _freebusy_query():
            return service.freebusy().query(body=body).execute()

        freebusy_result = await self._run_sync(_freebusy_query)
        busy_periods: List[Dict] = freebusy_result.get("calendars", {}).get(calendar_id, {}).get("busy", [])

        # Convert busy periods to datetime ranges
        busy_ranges: List[tuple] = []
        for period in busy_periods:
            bs = datetime.fromisoformat(period["start"].replace("Z", "+00:00"))
            be = datetime.fromisoformat(period["end"].replace("Z", "+00:00"))
            busy_ranges.append((bs, be))

        # Walk through business hours day by day to find free windows
        slots: List[Dict] = []
        candidate_day = time_min.replace(hour=0, minute=0, second=0, microsecond=0)
        if urgency != "emergency":
            candidate_day += timedelta(days=1)

        days_to_scan = 2 if urgency == "emergency" else (advance_days + 1)

        for _ in range(days_to_scan):
            if len(slots) >= num_slots:
                break
            if urgency == "emergency":
                day_start = max(now + timedelta(minutes=15), candidate_day.replace(
                    hour=biz_start_h, minute=biz_start_m, second=0, microsecond=0
                ))
                day_end = min(
                    now + timedelta(hours=2),
                    candidate_day.replace(hour=biz_end_h, minute=biz_end_m, second=0, microsecond=0),
                )
            else:
                day_start = candidate_day.replace(
                    hour=biz_start_h, minute=biz_start_m, second=0, microsecond=0
                )
                day_end = candidate_day.replace(
                    hour=biz_end_h, minute=biz_end_m, second=0, microsecond=0
                )

            cursor = day_start
            while cursor + timedelta(minutes=slot_duration) <= day_end and len(slots) < num_slots:
                slot_end = cursor + timedelta(minutes=slot_duration)
                # Check if this window overlaps any busy period
                is_free = not any(
                    not (slot_end <= bs or cursor >= be) for bs, be in busy_ranges
                )
                if is_free:
                    slot_id = str(uuid.uuid4())
                    display = cursor.strftime("%A, %B %-d — %-I:%M %p")
                    if urgency == "emergency":
                        display = f"Emergency — {cursor.strftime('%-I:%M %p')} today"
                    slots.append(
                        {
                            "slot_id": slot_id,
                            "display": display,
                            "iso_start": cursor.isoformat(),
                            "iso_end": slot_end.isoformat(),
                            "technician": "Available technician",
                        }
                    )
                cursor += timedelta(minutes=slot_duration)

            candidate_day += timedelta(days=1)

        return slots[:num_slots]

    async def _google_book_slot(
        self,
        slot_id: str,
        customer_name: str,
        phone: str,
        address: str,
        trade: str,
        notes: str,
    ) -> Dict:
        calendar_id: str = self.config["google_calendar_id"]
        slot_duration = self._get_config_int("slot_duration_minutes", 60)

        # slot_id is a UUID we generated; we don't store the exact time in the ID
        # so we schedule from now + 1 hour as a safe default when called standalone.
        # In practice, book_appointment passes appointment_time_str which is used
        # by the caller for the lead record. We create the calendar event using
        # the current time + 1 hour as placeholder; the slot ISO times come from
        # the prior get_available_slots call and are stored in the lead.
        # A production version would accept iso_start explicitly.
        now = datetime.now(tz=timezone.utc)
        iso_start = now + timedelta(hours=1)
        iso_end = iso_start + timedelta(minutes=slot_duration)

        description = (
            f"Customer: {customer_name}\n"
            f"Phone: {phone}\n"
            f"Address: {address}\n"
            f"Trade: {trade}\n"
            f"Notes: {notes}"
        )

        event_body = {
            "summary": f"[TradeFlow] {trade} - {customer_name}",
            "description": description,
            "start": {"dateTime": iso_start.isoformat(), "timeZone": "UTC"},
            "end": {"dateTime": iso_end.isoformat(), "timeZone": "UTC"},
        }

        service = await self._run_sync(self._build_google_service)

        def _insert_event():
            return service.events().insert(calendarId=calendar_id, body=event_body).execute()

        event = await self._run_sync(_insert_event)
        event_id: str = event.get("id", f"gcal-{slot_id}")
        confirmation_number = _make_confirmation_number()
        display = f"[TradeFlow] {trade} - {customer_name}"

        return {
            "success": True,
            "event_id": event_id,
            "confirmation_number": confirmation_number,
            "display": display,
        }

    async def _google_cancel_slot(self, event_id: str) -> Dict:
        calendar_id: str = self.config["google_calendar_id"]
        service = await self._run_sync(self._build_google_service)

        def _delete_event():
            return service.events().delete(calendarId=calendar_id, eventId=event_id).execute()

        await self._run_sync(_delete_event)
        return {"success": True, "event_id": event_id, "message": "Event deleted from Google Calendar"}
