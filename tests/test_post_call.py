from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.call import CallSession
from app.models.contractor import Contractor
from app.models.lead import Lead


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def contractor():
    c = MagicMock(spec=Contractor)
    c.id = uuid.uuid4()
    c.name = "Fix-It Fast Plumbing"
    c.phone_number = "+15559876543"
    c.sms_enabled = True
    c.review_link = "https://g.page/fixitfast"
    c.is_active = True
    c.plan = "starter"
    c.calls_this_month = 0
    c.sms_this_month = 0
    c.minutes_this_month = 0
    c.billing_period_start = None
    return c


@pytest.fixture()
def lead():
    lead_id = uuid.uuid4()
    ld = MagicMock(spec=Lead)
    ld.id = lead_id
    ld.phone = "+15551234567"
    ld.caller_name = "John Smith"
    ld.appointment_status = "not_booked"
    ld.ai_summary = None
    ld.sentiment = None
    ld.follow_up_recommended = False
    ld.review_requested = False
    return ld


@pytest.fixture()
def call_session(contractor, lead):
    cs = MagicMock(spec=CallSession)
    cs.id = uuid.uuid4()
    cs.retell_call_id = "call-post-call-test"
    cs.contractor_id = contractor.id
    cs.lead_id = lead.id
    return cs


@pytest.fixture()
def mock_db(lead):
    db = AsyncMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.add = MagicMock()
    # Return the lead when queried
    scalar_mock = MagicMock(scalar_one_or_none=MagicMock(return_value=lead))
    db.execute = AsyncMock(return_value=scalar_mock)
    return db


def _make_claude_response(payload: dict) -> MagicMock:
    """Build a mock httpx response that looks like an Anthropic API response."""
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = {
        "content": [{"text": json.dumps(payload)}],
    }
    return response


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_analyse_updates_lead_fields(call_session, contractor, mock_db, lead):
    """analyse() should set ai_summary, sentiment, and follow_up_recommended on the lead."""
    from app.services.post_call import PostCallAnalyser

    claude_payload = {
        "summary": "Customer called about a burst pipe.",
        "sentiment": "neutral",
        "follow_up_recommended": True,
        "review_recommended": False,
        "notes": "Urgent repair needed.",
    }

    mock_response = _make_claude_response(claude_payload)

    with patch("httpx.AsyncClient") as MockClient:
        instance = AsyncMock()
        instance.post = AsyncMock(return_value=mock_response)
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=None)
        MockClient.return_value = instance

        analyser = PostCallAnalyser()
        result = await analyser.analyse(call_session, "User: I have a burst pipe.", contractor, mock_db)

    assert result["success"] is True
    assert result["sentiment"] == "neutral"
    assert result["summary"] == "Customer called about a burst pipe."
    assert result["sms_sent"] is False

    # Lead fields should have been updated
    assert lead.ai_summary == "Customer called about a burst pipe."
    assert lead.sentiment == "neutral"
    assert lead.follow_up_recommended is True


@pytest.mark.asyncio
async def test_analyse_sends_review_sms_when_recommended(call_session, contractor, mock_db, lead):
    """When review_recommended=True and contractor has review_link, send_review_request is called."""
    from app.services.post_call import PostCallAnalyser

    claude_payload = {
        "summary": "Great call, customer very happy.",
        "sentiment": "positive",
        "follow_up_recommended": False,
        "review_recommended": True,
        "notes": "",
    }

    mock_response = _make_claude_response(claude_payload)

    with patch("httpx.AsyncClient") as MockClient, \
         patch("app.services.post_call.SMSService") as MockSMS:

        instance = AsyncMock()
        instance.post = AsyncMock(return_value=mock_response)
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=None)
        MockClient.return_value = instance

        sms_instance = MagicMock()
        sms_instance.send_review_request = MagicMock(return_value={"success": True})
        MockSMS.return_value = sms_instance

        analyser = PostCallAnalyser()
        result = await analyser.analyse(
            call_session, "User: Very happy with the service!", contractor, mock_db
        )

    assert result["success"] is True
    assert result["sms_sent"] is True
    assert result["sentiment"] == "positive"

    sms_instance.send_review_request.assert_called_once_with(
        phone=lead.phone,
        name=lead.caller_name,
        review_link=contractor.review_link,
    )


@pytest.mark.asyncio
async def test_analyse_no_sms_when_review_not_recommended(call_session, contractor, mock_db, lead):
    """When review_recommended=False, no SMS should be sent."""
    from app.services.post_call import PostCallAnalyser

    claude_payload = {
        "summary": "Customer had concerns.",
        "sentiment": "negative",
        "follow_up_recommended": True,
        "review_recommended": False,
        "notes": "Customer unhappy with pricing.",
    }

    mock_response = _make_claude_response(claude_payload)

    with patch("httpx.AsyncClient") as MockClient, \
         patch("app.services.post_call.SMSService") as MockSMS:

        instance = AsyncMock()
        instance.post = AsyncMock(return_value=mock_response)
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=None)
        MockClient.return_value = instance

        sms_instance = MagicMock()
        MockSMS.return_value = sms_instance

        analyser = PostCallAnalyser()
        result = await analyser.analyse(call_session, "User: Too expensive.", contractor, mock_db)

    assert result["success"] is True
    assert result["sms_sent"] is False
    sms_instance.send_review_request.assert_not_called()


@pytest.mark.asyncio
async def test_analyse_handles_claude_api_failure(call_session, contractor, mock_db):
    """If Claude API fails, analyse() should return success=False without raising."""
    from app.services.post_call import PostCallAnalyser

    with patch("httpx.AsyncClient") as MockClient:
        instance = AsyncMock()
        instance.post = AsyncMock(side_effect=Exception("Connection refused"))
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=None)
        MockClient.return_value = instance

        analyser = PostCallAnalyser()
        result = await analyser.analyse(call_session, "Transcript text", contractor, mock_db)

    assert result["success"] is False
    assert "error" in result


@pytest.mark.asyncio
async def test_analyse_handles_json_parse_failure(call_session, contractor, mock_db):
    """If Claude returns non-JSON, analyse() should return success=False."""
    from app.services.post_call import PostCallAnalyser

    bad_response = MagicMock()
    bad_response.raise_for_status = MagicMock()
    bad_response.json.return_value = {
        "content": [{"text": "Sorry, I cannot analyze this."}],
    }

    with patch("httpx.AsyncClient") as MockClient:
        instance = AsyncMock()
        instance.post = AsyncMock(return_value=bad_response)
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=None)
        MockClient.return_value = instance

        analyser = PostCallAnalyser()
        result = await analyser.analyse(call_session, "Transcript text", contractor, mock_db)

    assert result["success"] is False


@pytest.mark.asyncio
async def test_analyse_parses_json_code_fence(call_session, contractor, mock_db, lead):
    """analyse() should correctly extract JSON from ```json ... ``` code fence."""
    from app.services.post_call import PostCallAnalyser

    payload = {
        "summary": "Customer wants a quote.",
        "sentiment": "positive",
        "follow_up_recommended": False,
        "review_recommended": False,
        "notes": "",
    }
    fenced_text = f"```json\n{json.dumps(payload)}\n```"

    fenced_response = MagicMock()
    fenced_response.raise_for_status = MagicMock()
    fenced_response.json.return_value = {"content": [{"text": fenced_text}]}

    with patch("httpx.AsyncClient") as MockClient:
        instance = AsyncMock()
        instance.post = AsyncMock(return_value=fenced_response)
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=None)
        MockClient.return_value = instance

        analyser = PostCallAnalyser()
        result = await analyser.analyse(call_session, "User: I need a quote.", contractor, mock_db)

    assert result["success"] is True
    assert result["summary"] == "Customer wants a quote."
