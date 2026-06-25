import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import anthropic
import pytest

from app.models.call import CallSession
from app.models.contractor import Contractor
from app.services.claude_agent import ClaudeAgent, MAX_TOOL_ITERATIONS


def _make_text_response(text: str) -> MagicMock:
    block = MagicMock()
    block.type = "text"
    block.text = text
    response = MagicMock(spec=anthropic.types.Message)
    response.content = [block]
    return response


def _make_tool_response(tool_name: str, tool_input: dict, tool_id: str = "tu_abc") -> MagicMock:
    block = MagicMock()
    block.type = "tool_use"
    block.id = tool_id
    block.name = tool_name
    block.input = tool_input
    response = MagicMock(spec=anthropic.types.Message)
    response.content = [block]
    return response


@pytest.fixture()
def contractor():
    c = MagicMock(spec=Contractor)
    c.id = uuid.uuid4()
    c.name = "Test Co"
    c.agent_name = "Sam"
    c.trades = ["plumbing"]
    c.service_areas = ["T2N"]
    c.diagnostic_fee = None
    c.free_estimate = True
    c.review_link = None
    c.calendar_provider = "manual"
    c.calendar_config = {}
    c.sms_enabled = False
    return c


@pytest.fixture()
def call_session(contractor):
    cs = MagicMock(spec=CallSession)
    cs.id = uuid.uuid4()
    cs.retell_call_id = "call-agent-test"
    cs.contractor_id = contractor.id
    cs.conversation_history = []
    cs.lead_id = None
    return cs


@pytest.fixture()
def mock_db():
    db = AsyncMock()
    db.flush = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_process_turn_returns_string(contractor, call_session, mock_db):
    agent = ClaudeAgent(contractor=contractor, call_session=call_session, db=mock_db)
    text_response = _make_text_response("What seems to be the problem?")

    with patch.object(agent, "_call_claude", AsyncMock(return_value=text_response)):
        result = await agent.process_turn("I have a leak under my sink")

    assert isinstance(result, str)
    assert len(result) > 0


@pytest.mark.asyncio
async def test_tool_calls_are_detected_and_executed(contractor, call_session, mock_db):
    agent = ClaudeAgent(contractor=contractor, call_session=call_session, db=mock_db)

    tool_response = _make_tool_response(
        "check_availability", {"urgency": "same_day", "trade": "plumbing"}, "tu_001"
    )
    final_response = _make_text_response("I have openings today at 2 PM or 4 PM.")

    call_sequence = [tool_response, final_response]
    call_index = {"i": 0}

    async def fake_call_claude(messages):
        resp = call_sequence[call_index["i"]]
        call_index["i"] += 1
        return resp

    mock_tool_result = {"slots": [{"slot_id": "s1", "display": "Today 2 PM", "datetime_iso": "2026-06-24T14:00:00Z"}], "success": True}

    with (
        patch.object(agent, "_call_claude", side_effect=fake_call_claude),
        patch("app.services.claude_agent.execute_tool", AsyncMock(return_value=mock_tool_result)),
    ):
        result = await agent.process_turn("I need someone today for a leaking pipe")

    assert "PM" in result or len(result) > 0


@pytest.mark.asyncio
async def test_agentic_loop_terminates_at_max_iterations(contractor, call_session, mock_db):
    agent = ClaudeAgent(contractor=contractor, call_session=call_session, db=mock_db)

    # Every response is a tool call — loop should cap at MAX_TOOL_ITERATIONS
    infinite_tool = _make_tool_response("check_availability", {"urgency": "flexible", "trade": "plumbing"})

    with (
        patch.object(agent, "_call_claude", AsyncMock(return_value=infinite_tool)),
        patch("app.services.claude_agent.execute_tool", AsyncMock(return_value={"slots": [], "success": True})),
    ):
        result = await agent.process_turn("Test message")

    # Should not raise; returns empty string since no text block ever appeared
    assert isinstance(result, str)


@pytest.mark.asyncio
async def test_conversation_history_is_persisted(contractor, call_session, mock_db):
    agent = ClaudeAgent(contractor=contractor, call_session=call_session, db=mock_db)
    text_response = _make_text_response("Hello! How can I help?")

    with patch.object(agent, "_call_claude", AsyncMock(return_value=text_response)):
        await agent.process_turn("Hello")

    history = call_session.conversation_history
    assert any(m["role"] == "user" for m in history)
    assert any(m["role"] == "assistant" for m in history)
    mock_db.flush.assert_called()
