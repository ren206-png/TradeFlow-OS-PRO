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
async def test_send_sms_calls_twilio_with_correct_params(ctx):
    from app.tools.send_sms import send_sms

    mock_twilio_result = {"success": True, "sid": "SM_test_123"}

    with patch("app.tools.send_sms.SMSService") as MockSMS:
        instance = MagicMock()
        instance.send_booking_confirmation = MagicMock(return_value=mock_twilio_result)
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
