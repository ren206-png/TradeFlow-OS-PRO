import uuid
from datetime import datetime, timedelta, timezone


async def check_availability(tool_input: dict, context: dict) -> dict:
    """Return available appointment slots. MVP uses generated slots; stubs for live calendar providers."""
    contractor = context["contractor"]
    urgency = tool_input["urgency"]
    trade = tool_input["trade"]

    if contractor.calendar_provider == "google":
        # TODO: integrate Google Calendar API
        slots = _generate_slots(urgency)
    elif contractor.calendar_provider == "calendly":
        # TODO: integrate Calendly API
        slots = _generate_slots(urgency)
    else:
        slots = _generate_slots(urgency)

    return {"slots": slots, "trade": trade, "success": True}


def _generate_slots(urgency: str) -> list[dict]:
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
            "datetime_iso": slot_times[i].isoformat(),
        }
        for i in range(len(slot_times))
    ]
