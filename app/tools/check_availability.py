from __future__ import annotations


async def check_availability(tool_input: dict, context: dict) -> dict:
    """Return available appointment slots via CalendarService."""
    from app.services.calendar import CalendarService

    contractor = context["contractor"]
    urgency = tool_input["urgency"]
    trade = tool_input["trade"]

    service = CalendarService(contractor)
    slots = await service.get_available_slots(trade=trade, urgency=urgency, num_slots=3)

    return {"slots": slots, "trade": trade, "success": True}
