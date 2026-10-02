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


@pytest.mark.asyncio
async def test_empty_turn_after_transfer_says_connecting():
    contractor = _make_contractor()
    session = CallSession(retell_call_id="call_t", contractor_id=contractor.id, status="active",
                          conversation_history=[])
    agent = ClaudeAgent(contractor=contractor, call_session=session, db=AsyncMock())
    transfer = MagicMock(); transfer.type = "tool_use"; transfer.name = "transfer_call"; transfer.id = "tu1"
    transfer.input = {"reason": "caller_requested"}
    lead = MagicMock(); lead.type = "tool_use"; lead.name = "create_lead_record"; lead.id = "tu2"; lead.input = {}
    responses = [MagicMock(content=[transfer]), MagicMock(content=[lead]), MagicMock(content=[])]
    with patch.object(agent, "_call_claude", AsyncMock(side_effect=responses)), \
         patch("app.services.claude_agent.execute_tool", AsyncMock(return_value={"success": True})), \
         patch("app.services.claude_agent._serialize_content",
               side_effect=lambda c: [{"type": b.type, "name": b.name, "id": b.id, "input": b.input} for b in c]):
        assert await agent.process_turn("get me a person") == "Let me connect you with someone now."


def test_cache_marker_goes_on_last_block_without_mutating_history():
    from app.services.claude_agent import _with_cache_marker

    history = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": [{"type": "text", "text": "hi"}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "{}"}]},
    ]
    out = _with_cache_marker(history)
    assert out[-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in history[-1]["content"][-1]  # stored history untouched
    assert _with_cache_marker([{"role": "user", "content": "hey"}])[0]["content"][0]["cache_control"]
    assert _with_cache_marker([]) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["missed_call", "followup", "review_request", "appointment_reminder"])
async def test_ai_cannot_send_non_confirmation_texts(db, kind):
    """A mid-call 'sorry we missed you' text went to a customer who was on the phone."""
    from app.tools.send_sms import send_sms
    contractor = _make_contractor()
    session = CallSession(retell_call_id="call_sms1", contractor_id=contractor.id, status="active", conversation_history=[])
    db.add_all([contractor, session])
    await db.commit()
    with patch("app.tools.send_sms.SMSService") as sms_cls:
        result = await send_sms({"to_number": "+14035550142", "message_type": kind},
                                {"db": db, "call_session": session, "contractor": contractor})
    assert result["success"] is False
    sms_cls.assert_not_called()


@pytest.mark.asyncio
async def test_two_connections_for_one_call_share_one_lead(db):
    """Each Retell connection has its own CallSession object; the second must find the first one's lead."""
    from sqlalchemy import func, select
    from app.tools.create_lead import create_lead_record

    contractor = _make_contractor()
    db.add(contractor)
    await db.commit()
    await db.refresh(contractor)

    def conn_session():
        s = CallSession(retell_call_id="call_dup1", contractor_id=contractor.id, status="active", conversation_history=[])
        return s

    first = conn_session()
    db.add(first)
    await db.commit()
    with patch("app.tools.create_lead.notify_new_lead", new=AsyncMock()):
        await create_lead_record({"caller_name": "Taylor", "phone": "403-555-0142"},
                                 {"db": db, "call_session": first, "contractor": contractor})
        await db.commit()
        stale = CallSession(id=first.id, retell_call_id="call_dup1", contractor_id=contractor.id,
                            status="active", conversation_history=[], lead_id=None)
        await create_lead_record({"caller_name": "Taylor Test", "service_address": "123 Test St"},
                                 {"db": db, "call_session": stale, "contractor": contractor})
        await db.commit()
    count = (await db.execute(select(func.count()).select_from(Lead).where(Lead.call_id == "call_dup1"))).scalar_one()
    assert count == 1
    lead = (await db.execute(select(Lead).where(Lead.call_id == "call_dup1"))).scalar_one()
    assert lead.caller_name == "Taylor Test" and lead.service_address == "123 Test St" and lead.phone == "+14035550142"


@pytest.mark.asyncio
@pytest.mark.parametrize("areas,city,postal,expected", [
    (["Edmonton, AB"], "Edmonton", "T5J 1A1", "inside"),        # "City, PROV" area vs plain city
    (["Edmonton, AB"], "Edmonton, AB", "T5J 1A1", "inside"),
    (["Calgary", "T2N"], "Banff", "T2N 1A1", "inside"),         # FSA match
    (["Edmonton, AB"], "Calgary", "T2N 1A1", "outside"),
])
async def test_service_area_matching(areas, city, postal, expected):
    from app.tools.validate_address import validate_service_area
    contractor = _make_contractor()
    contractor.service_areas = areas
    result = await validate_service_area({"postal_zip": postal, "city": city}, {"contractor": contractor})
    assert result["status"] == expected


@pytest.mark.asyncio
async def test_demo_line_accepts_any_address(monkeypatch):
    from app.config import settings
    from app.tools.validate_address import validate_service_area
    contractor = _make_contractor()
    contractor.service_areas = ["Demo City, CA"]
    monkeypatch.setattr(settings, "demo_contractor_id", str(contractor.id))
    result = await validate_service_area({"postal_zip": "T2N 1A1", "city": "Calgary"}, {"contractor": contractor})
    assert result["status"] == "inside"


def test_goodbye_backstop_only_after_a_lead_exists():
    from types import SimpleNamespace
    from app.routers.retell import _caller_said_goodbye

    with_lead = SimpleNamespace(call_session=SimpleNamespace(lead_id="x"))
    no_lead = SimpleNamespace(call_session=SimpleNamespace(lead_id=None))
    assert _caller_said_goodbye("No, that's all. Thank you. Bye.", with_lead)
    assert _caller_said_goodbye("Goodbye", with_lead)
    assert not _caller_said_goodbye("Goodbye", no_lead)                 # never hang up before capturing the caller
    assert not _caller_said_goodbye("My name is Bye Smith and I need a plumber for the kitchen sink please", with_lead)
    assert not _caller_said_goodbye("", with_lead)


@pytest.mark.asyncio
async def test_end_call_tool_flags_the_call_for_hangup():
    from app.tools.handlers import execute_tool
    ctx = {}
    result = await execute_tool("end_call", {}, ctx)
    assert result["success"] and ctx["end_call"] is True
