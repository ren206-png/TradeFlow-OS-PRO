from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import List


async def check_availability(tool_input: dict, context: dict) -> dict:
    """Return available appointment slots via CalendarService."""
    from app.services.calendar import CalendarService

    contractor = context["contractor"]
    urgency = tool_input["urgency"]
    trade = tool_input["trade"]

    service = CalendarService(contractor)
    slots = await service.get_available_slots(trade=trade, urgency=urgency, num_slots=3)

    return {"slots": slots, "trade": trade, "success": True}


def _generate_slots(urgency: str) -> List[dict]:
    """Legacy helper retained for backward compatibility."""
    now = datetime.now(tz=timezone.utc)

    if urgency == "emergency":
        slot_times = [now + timedelta(hours=1)]
        labels = ["Emergency slot — within 1 hour"]
    elif urgency == "same_day":
        slot_times = [now + timedelta(hours=4), now + timedelta(hours=6)]
        labels = ["Today — afternoon", "Today — early evening"]
    elif urgency == "next_day":
        tomorrow = now + timedelta(days=1)
        slot_times = [
            tomorrow.replace(hour=9, minute=0, second=0, microsecond=0),
            tomorrow.replace(hour=13, minute=0, second=0, microsecond=0),
        ]
        labels = ["Tomorrow — morning (9 AM)", "Tomorrow — afternoon (1 PM)"]
    else:
        base = now + timedelta(days=1)
        slot_times = [
            base.replace(hour=9, minute=0, second=0, microsecond=0),
            (base + timedelta(days=1)).replace(hour=13, minute=0, second=0, microsecond=0),
            (base + timedelta(days=6)).replace(hour=10, minute=0, second=0, microsecond=0),
        ]
        labels = [
            f"{(now + timedelta(days=1)).strftime('%A')} — morning (9 AM)",
            f"{(now + timedelta(days=2)).strftime('%A')} — afternoon (1 PM)",
            f"Next {(now + timedelta(days=7)).strftime('%A')} — morning (10 AM)",
        ]

    return [
        {
            "slot_id": str(uuid.uuid4()),
            "display": labels[i],
            "iso_start": slot_times[i].isoformat(),
            "iso_end": (slot_times[i] + timedelta(hours=1)).isoformat(),
            "technician": "Available technician",
        }
        for i in range(len(slot_times))
    ]
