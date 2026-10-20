import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.call import CallSession
from app.models.contractor import Contractor


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def contractor():
    c = MagicMock(spec=Contractor)
    c.id = uuid.uuid4()
    c.name = "Fix-It Fast Plumbing"
    c.agent_name = "Jordan"
    c.phone_number = "+15559876543"
    c.trades = ["plumbing"]
    c.service_areas = ["T2N", "T2P", "Calgary", "90210"]
    c.diagnostic_fee = 89.0
    c.free_estimate = False
    c.calendar_provider = "manual"
    c.calendar_config = {}
    c.sms_enabled = True
    c.review_link = "https://g.page/fixitfast"
    c.is_active = True
    c.plan = "starter"
    c.calls_this_month = 0
    c.sms_this_month = 0
    return c


@pytest.fixture()
def call_session(contractor):
    cs = MagicMock(spec=CallSession)
    cs.id = uuid.uuid4()
    cs.retell_call_id = "call-tools-test"
    cs.contractor_id = contractor.id
    cs.conversation_history = []
    cs.lead_id = None
    return cs


@pytest.fixture()
def mock_db():
    db = AsyncMock()
    db.flush = AsyncMock()
    db.add = MagicMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
    return db


@pytest.fixture()
def ctx(contractor, call_session, mock_db):
    return {"contractor": contractor, "call_session": call_session, "db": mock_db}


# ---------------------------------------------------------------------------
# check_availability
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_check_availability_returns_slots(ctx):
    from app.tools.check_availability import check_availability

    result = await check_availability({"urgency": "flexible", "trade": "plumbing"}, ctx)

    assert result["success"] is True
    assert isinstance(result["slots"], list)
    assert len(result["slots"]) > 0

    slot = result["slots"][0]
    assert "slot_id" in slot
    assert "display" in slot
    assert "iso_start" in slot


@pytest.mark.asyncio
async def test_check_availability_emergency_returns_soonest_slot(ctx):
    from app.tools.check_availability import check_availability

    result = await check_availability({"urgency": "emergency", "trade": "plumbing"}, ctx)

    assert result["success"] is True
    assert len(result["slots"]) == 1
    assert "Emergency" in result["slots"][0]["display"]


@pytest.mark.asyncio
async def test_check_availability_same_day_returns_multiple(ctx):
    from app.tools.check_availability import check_availability

    result = await check_availability({"urgency": "same_day", "trade": "hvac"}, ctx)

    assert len(result["slots"]) >= 2


# ---------------------------------------------------------------------------
# validate_service_area
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_validate_service_area_inside_exact_match(ctx):
    from app.tools.validate_address import validate_service_area

    result = await validate_service_area({"postal_zip": "T2N 1A1"}, ctx)
    assert result["status"] == "inside"


@pytest.mark.asyncio
async def test_validate_service_area_inside_fsa_match(ctx):
    from app.tools.validate_address import validate_service_area

    # T2P matches because "T2P" is in service_areas
    result = await validate_service_area({"postal_zip": "T2P 3B2"}, ctx)
    assert result["status"] == "inside"


@pytest.mark.asyncio
async def test_validate_service_area_inside_city_match(ctx):
    from app.tools.validate_address import validate_service_area

    result = await validate_service_area({"postal_zip": "ZZZZZ", "city": "Calgary"}, ctx)
    assert result["status"] == "inside"


@pytest.mark.asyncio
async def test_validate_service_area_outside(ctx):
    from app.tools.validate_address import validate_service_area

    result = await validate_service_area({"postal_zip": "V6B 1A1", "city": "Vancouver"}, ctx)
    assert result["status"] == "outside"


@pytest.mark.asyncio
async def test_validate_service_area_us_zip_match(ctx):
    from app.tools.validate_address import validate_service_area

    result = await validate_service_area({"postal_zip": "90210"}, ctx)
    assert result["status"] == "inside"


# ---------------------------------------------------------------------------
# send_sms
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_send_sms_calls_provider_with_correct_params(ctx):
    from app.tools.send_sms import send_sms

    mock_sms_result = {"success": True, "sid": "SM_test_123"}

    with patch("app.tools.send_sms.SMSService") as MockSMS:
        instance = MagicMock()
        instance.send_booking_confirmation = MagicMock(return_value=mock_sms_result)
        MockSMS.return_value = instance

        result = await send_sms(
            {
                "to_number": "+15551234567",
                "message_type": "booking_confirmation",
                "name": "John Smith",
                "trade": "plumbing",
                "date_str": "2026-06-25",
                "time_str": "2:00 PM",
                "address": "123 Main St",
            },
            ctx,
        )

    assert result["success"] is True
    assert result["sid"] == "SM_test_123"
    instance.send_booking_confirmation.assert_called_once_with(
        phone="+15551234567",
        name="John Smith",
        trade="plumbing",
        date_str="2026-06-25",
        time_str="2:00 PM",
        address="123 Main St",
    )


# ---------------------------------------------------------------------------
# create_lead_record
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_lead_record_upserts_correctly(ctx):
    from app.tools.create_lead import create_lead_record

    result = await create_lead_record(
        {
            "caller_name": "Jane Smith",
            "phone": "+15559990000",
            "trade": "plumbing",
            "problem_summary": "burst pipe under sink",
            "service_area_status": "inside",
            "appointment_status": "booked",
            "call_direction": "inbound",
            "emergency_score": 8,
            "revenue_score": 6,
        },
        ctx,
    )

    assert result["success"] is True
    assert "lead_id" in result
    ctx["db"].add.assert_called_once()
    ctx["db"].flush.assert_called()


@pytest.mark.asyncio
async def test_create_lead_record_required_field_only(ctx):
    from app.tools.create_lead import create_lead_record

    result = await create_lead_record({"call_direction": "inbound"}, ctx)

    assert result["success"] is True
    assert "lead_id" in result


def _real_context(db_obj):
    import uuid
    from app.models.call import CallSession
    from app.models.contractor import Contractor
    c = Contractor(id=uuid.uuid4(), name="Clip Plumbing", agent_name="Alex", phone_number="+15550007777",
                   api_key="k-clip-" + uuid.uuid4().hex, trades=["plumbing"], service_areas=["Edmonton"], timezone="America/Edmonton")
    cs = CallSession(id=uuid.uuid4(), retell_call_id="call-clip-1", contractor_id=c.id, status="active", conversation_history=[])
    db_obj.add_all([c, cs])
    return c, cs


@pytest.mark.asyncio
async def test_overlong_ai_text_is_clipped_not_fatal(db):
    # Regression (owner-transfer test, 2026-10-18): a 58-character customer_sentiment overflowed VARCHAR(50),
    # the save failed, and the call's database session was left unusable.
    from sqlalchemy import select
    from app.models.lead import Lead
    from app.tools.create_lead import create_lead_record
    c, cs = _real_context(db)
    await db.commit()
    ctx = {"db": db, "contractor": c, "call_session": cs, "caller_phone": "+14035550142"}
    await create_lead_record({"caller_name": "Sam", "customer_sentiment": "Panicked, urgent, repeated requests for immediate dispatch" * 2,
                              "emergency_level": "x" * 90}, ctx)
    await db.flush()
    lead = (await db.execute(select(Lead).where(Lead.call_id == "call-clip-1"))).scalar_one()
    assert len(lead.customer_sentiment) <= 50 and len(lead.emergency_level) <= 50


@pytest.mark.asyncio
async def test_failed_tool_does_not_break_the_session(db, monkeypatch):
    from sqlalchemy import select
    from app.models.contractor import Contractor
    from app.tools import handlers
    c, cs = _real_context(db)
    await db.commit()

    async def bad_tool(tool_input, context):
        context["db"].add(Contractor(name="dup", agent_name="x", phone_number=c.phone_number, api_key="k-dup"))  # unique violation
        await context["db"].flush()

    monkeypatch.setitem(handlers._TOOL_MAP, "bad_tool", bad_tool)
    out = await handlers.execute_tool("bad_tool", {}, {"db": db, "contractor": c, "call_session": cs})
    assert out["success"] is False
    assert cs.retell_call_id == "call-clip-1"  # still readable: no lazy-load on a broken session
    assert (await db.execute(select(Contractor).where(Contractor.id == c.id))).scalar_one()


@pytest.mark.asyncio
async def test_invalid_spoken_number_falls_back_to_caller_id(db):
    # Regression (weekly voice test, 2026-10-19): the AI heard "78555142" and saved it as the callback number.
    from sqlalchemy import select
    from app.models.lead import Lead
    from app.tools.create_lead import create_lead_record
    c, cs = _real_context(db)
    await db.commit()
    ctx = {"db": db, "contractor": c, "call_session": cs, "caller_phone": "+15878001544"}
    await create_lead_record({"caller_name": "Taylor", "phone": "78555142"}, ctx)
    await db.flush()
    lead = (await db.execute(select(Lead).where(Lead.call_id == "call-clip-1"))).scalar_one()
    assert lead.phone == "+15878001544" and "78555142" in (lead.notes or "")
    # a valid spoken number is kept (and normalised)
    await create_lead_record({"phone": "(780) 555-0142"}, ctx)
    await db.flush()
    await db.refresh(lead)
    assert lead.phone == "+17805550142"


def test_only_real_emergencies_alert_the_owner_mid_call():
    # Regression (daily simulation, 2026-10-19): a routine job scored "High" and texted the owner at creation,
    # then texted again at booking.
    from types import SimpleNamespace
    from app.tools.create_lead import _is_urgent
    routine = SimpleNamespace(priority_level="High", emergency_level="standard", life_safety_risk=False)
    assert _is_urgent(routine) is False
    assert _is_urgent(SimpleNamespace(priority_level="Critical", emergency_level="standard", life_safety_risk=False)) is True
    assert _is_urgent(SimpleNamespace(priority_level="Medium", emergency_level="emergency", life_safety_risk=False)) is True
    assert _is_urgent(SimpleNamespace(priority_level="Low", emergency_level="", life_safety_risk=True)) is True
