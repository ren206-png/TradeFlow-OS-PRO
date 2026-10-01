"""Speech output, booking time/phone handling, and confirmation dedupe found by the call simulator."""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.call import CallSession
from app.models.lead import Lead
from app.services.calendar import CalendarService, issued_slot
from app.services.claude_agent import ClaudeAgent, to_spoken_text
from tests.test_auth import _make_contractor


def test_to_spoken_text_strips_markdown_lists_and_emoji():
    raw = "We have:\n- **8:00 AM**\n- **10:00 AM**\nWhich works? 😊"
    assert to_spoken_text(raw) == "We have: 8:00 AM, 10:00 AM, Which works?"
    assert to_spoken_text('"Hi there!"') == "Hi there!"


@pytest.mark.asyncio
async def test_empty_model_turn_never_returns_dead_air():
    contractor = _make_contractor()
    session = CallSession(retell_call_id="call_x", contractor_id=contractor.id, status="active",
                          conversation_history=[])
    agent = ClaudeAgent(contractor=contractor, call_session=session, db=AsyncMock())
    empty = MagicMock(content=[])
    with patch.object(agent, "_call_claude", AsyncMock(return_value=empty)):
        assert await agent.process_turn("hello") == "Sorry, could you say that one more time?"


@pytest.mark.asyncio
async def test_booking_recovers_time_from_offered_slot_and_normalizes_phone(db):
    from app.tools.book_appointment import book_appointment

    contractor = _make_contractor()
    session = CallSession(retell_call_id="call_book1", contractor_id=contractor.id, status="active",
                          conversation_history=[])
    db.add_all([contractor, session])
    await db.commit()
    slots = await CalendarService(contractor).get_available_slots("plumbing", "standard", 1)
    slot = slots[0]
    assert issued_slot(slot["slot_id"])["iso_start"] == slot["iso_start"]

    sent = []
    with patch("app.tools.send_sms.SMSService") as sms_cls, \
         patch("app.tools.book_appointment.notify_appointment_booked", new=AsyncMock()):
        sms_cls.return_value.send_booking_confirmation.side_effect = lambda **kw: sent.append(kw) or {"success": True}
        ctx = {"db": db, "call_session": session, "contractor": contractor, "caller_phone": "+14035550142"}
        result = await book_appointment({"slot_id": slot["slot_id"], "caller_name": "Jordan", "phone": "403-555-0142",
                                         "service_address": "1 Test St", "trade": "plumbing",
                                         "problem_summary": "dripping faucet"}, ctx)
        # The model sometimes also asks to send the confirmation itself — must not duplicate.
        from app.tools.send_sms import send_sms
        dup = await send_sms({"to_number": "403-555-0142", "message_type": "booking_confirmation"}, ctx)

    assert result["success"] and result["appointment_time"] == slot["iso_start"]
    lead = (await db.get(Lead, uuid.UUID(result["lead_id"])))
    assert lead.phone == "+14035550142" and lead.priority_level
    assert len(sent) == 1 and sent[0]["phone"] == "+14035550142" and sent[0]["date_str"] and sent[0]["time_str"]
    assert dup.get("skipped")
