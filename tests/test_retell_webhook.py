"""
Tests for the Retell integration:
  - HMAC webhook signature verification
  - HTTP webhook lifecycle events
  - WebSocket Custom LLM turn exchange
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from datetime import datetime, timezone
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.models.call import CallSession
from app.models.contractor import Contractor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sign(body: bytes, api_key: str = "test-api-key", timestamp_ms: Optional[int] = None) -> str:
    """
    Retell signature format: v={timestamp_ms},d={hmac_sha256(body+timestamp, api_key)}
    """
    ts = timestamp_ms if timestamp_ms is not None else int(time.time() * 1000)
    signing_data = body + str(ts).encode()
    digest = hmac.new(api_key.encode(), signing_data, hashlib.sha256).hexdigest()
    return f"v={ts},d={digest}"


def _signed_headers(body: bytes, api_key: str = "test-api-key") -> dict:
    return {"x-retell-signature": _sign(body, api_key), "Content-Type": "application/json"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def patch_retell_api_key(monkeypatch):
    monkeypatch.setattr(settings, "retell_api_key", "test-api-key")


@pytest.fixture()
def mock_contractor():
    c = MagicMock(spec=Contractor)
    c.id = uuid.uuid4()
    c.name = "ABC Plumbing"
    c.agent_name = "Alex"
    c.phone_number = "+15550001234"
    c.trades = ["plumbing"]
    c.service_areas = ["T2N"]
    c.timezone = "America/Edmonton"
    c.diagnostic_fee = 99.0
    c.free_estimate = False
    c.calendar_provider = "manual"
    c.calendar_config = {}
    c.sms_enabled = False
    c.review_link = None
    c.retell_agent_id = None
    c.is_active = True
    c.plan = "starter"
    c.calls_this_month = 0
    c.sms_this_month = 0
    return c


@pytest.fixture()
def mock_call_session(mock_contractor):
    cs = MagicMock(spec=CallSession)
    cs.id = uuid.uuid4()
    cs.retell_call_id = "call-test-001"
    cs.contractor_id = mock_contractor.id
    cs.conversation_history = []
    cs.lead_id = None
    cs.status = "active"
    return cs


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_malformed_signature_returns_403():
    from app.main import app

    body = json.dumps({"event": "call_ended", "call": {"call_id": "x"}}).encode()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/retell/webhook",
            content=body,
            headers={"x-retell-signature": "not-the-right-format", "Content-Type": "application/json"},
        )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_wrong_key_returns_403():
    from app.main import app

    body = json.dumps({"event": "call_ended", "call": {"call_id": "x"}}).encode()
    bad_sig = _sign(body, api_key="wrong-key")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/retell/webhook",
            content=body,
            headers={"x-retell-signature": bad_sig, "Content-Type": "application/json"},
        )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_stale_timestamp_returns_403():
    from app.main import app

    body = json.dumps({"event": "call_started", "call": {"call_id": "x"}}).encode()
    # Timestamp 10 minutes in the past
    old_ts = int(time.time() * 1000) - (10 * 60 * 1000)
    stale_sig = _sign(body, timestamp_ms=old_ts)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/retell/webhook",
            content=body,
            headers={"x-retell-signature": stale_sig, "Content-Type": "application/json"},
        )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_valid_signature_passes():
    from app.main import app

    payload = {"event": "call_started", "call": {"call_id": "x", "to_number": "+15550001234"}}
    body = json.dumps(payload).encode()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/retell/webhook",
            content=body,
            headers=_signed_headers(body),
        )
    # 200 or 404 (no contractor in test DB) — either way, not 403
    assert response.status_code != 403


# ---------------------------------------------------------------------------
# HTTP webhook — call_ended
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_call_ended_webhook_marks_session_complete(mock_call_session):
    from app.main import app
    from app.database import get_db

    mock_call_session.lead_id = None
    payload = {
        "event": "call_ended",
        "call": {
            "call_id": "call-ended-001",
            "start_timestamp": 1000000000,
            "end_timestamp": 1000180000,
            "recording_url": "https://example.com/rec.mp3",
            "public_log_url": "https://example.com/transcript.json",
        },
    }
    body = json.dumps(payload).encode()

    mock_db = AsyncMock()
    # _finalise_session: query 1 = call_session, query 2 = contractor (billing)
    # _schedule_post_call_jobs: query 3 = call_session again (lead_id=None → early return)
    # Use a callable so any extra queries also return the null result gracefully.
    _cs_result = MagicMock(scalar_one_or_none=MagicMock(return_value=mock_call_session))
    _no_result = MagicMock(scalar_one_or_none=MagicMock(return_value=None))
    _query_responses = [_cs_result, _no_result, _cs_result]

    async def _execute_side_effect(*_args, **_kwargs):
        return _query_responses.pop(0) if _query_responses else _no_result

    mock_db.execute = AsyncMock(side_effect=_execute_side_effect)
    mock_db.flush = AsyncMock()
    mock_db.commit = AsyncMock()
    mock_db.rollback = AsyncMock()

    async def _mock_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = _mock_get_db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/retell/webhook",
                content=body,
                headers=_signed_headers(body),
            )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert mock_call_session.status == "completed"
    assert mock_call_session.duration_seconds == 180


# ---------------------------------------------------------------------------
# WebSocket — Custom LLM turn exchange
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_websocket_call_details_triggers_greeting(mock_contractor, mock_call_session):
    from app.main import app
    from app.database import get_db

    mock_agent = MagicMock()
    mock_agent.process_turn = AsyncMock(return_value="ABC Plumbing, Alex speaking. How can I help?")
    mock_agent.call_session = mock_call_session

    # First execute → find contractor; second execute (on WS disconnect finalise) → None (early return)
    _no_result = MagicMock(scalar_one_or_none=MagicMock(return_value=None))
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=[
        MagicMock(scalar_one_or_none=MagicMock(return_value=mock_contractor)),
        _no_result,  # _finalise_session: call_session not found → early return
    ])
    mock_db.flush = AsyncMock()
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()
    mock_db.rollback = AsyncMock()

    async def _mock_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = _mock_get_db
    try:
        with patch("app.routers.retell.ClaudeAgent", return_value=mock_agent):
            from starlette.testclient import TestClient
            client = TestClient(app)
            with client.websocket_connect("/llm-websocket/call-ws-test") as ws:
                ws.receive_json()  # discard initial config event
                ws.send_json({
                    "interaction_type": "call_details",
                    "call": {
                        "call_id": "call-ws-test",
                        "from_number": "+15559998888",
                        "to_number": "+15550001234",
                        "metadata": {},
                    },
                })
                response = ws.receive_json()
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert "content" in response
    assert isinstance(response["content"], str)
    assert len(response["content"]) > 0
    assert response["content_complete"] is True


@pytest.mark.asyncio
async def test_websocket_response_required_returns_agent_text(mock_call_session):
    from app.routers.retell import _active_agents
    from app.main import app
    from app.database import get_db

    call_id = "call-ws-update-test"
    mock_agent = MagicMock()
    mock_agent.process_turn = AsyncMock(return_value="Can you describe where the leak is?")
    mock_agent.call_session = mock_call_session
    _active_agents[call_id] = mock_agent

    _no_result = MagicMock(scalar_one_or_none=MagicMock(return_value=None))
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=_no_result)  # _finalise_session returns early
    mock_db.commit = AsyncMock()
    mock_db.rollback = AsyncMock()

    async def _mock_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = _mock_get_db
    try:
        from starlette.testclient import TestClient
        client = TestClient(app)
        with client.websocket_connect(f"/llm-websocket/{call_id}") as ws:
            ws.receive_json()  # discard initial config event
            ws.send_json({
                "interaction_type": "response_required",
                "response_id": 1,
                "transcript": [{"role": "user", "content": "I have a leak under my sink"}],
            })
            response = ws.receive_json()
    finally:
        app.dependency_overrides.pop(get_db, None)
        _active_agents.pop(call_id, None)

    assert response["response_id"] == 1
    assert "Can you describe" in response["content"]
    assert response["content_complete"] is True


@pytest.mark.asyncio
async def test_websocket_call_update_returns_string_response():
    """Backward-compatibility alias: agent process_turn result is always a string."""
    from app.routers.retell import _active_agents
    from app.main import app
    from app.database import get_db

    call_id = "call-str-check"
    mock_agent = MagicMock()
    mock_agent.process_turn = AsyncMock(return_value="What is the service address?")
    mock_agent.call_session = MagicMock(conversation_history=[])
    _active_agents[call_id] = mock_agent

    _no_result = MagicMock(scalar_one_or_none=MagicMock(return_value=None))
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=_no_result)  # _finalise_session returns early
    mock_db.commit = AsyncMock()
    mock_db.rollback = AsyncMock()

    async def _mock_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = _mock_get_db
    try:
        from starlette.testclient import TestClient
        client = TestClient(app)
        with client.websocket_connect(f"/llm-websocket/{call_id}") as ws:
            ws.receive_json()  # discard initial config event
            ws.send_json({
                "interaction_type": "response_required",
                "response_id": 2,
                "transcript": [{"role": "user", "content": "burst pipe"}],
            })
            data = ws.receive_json()
    finally:
        app.dependency_overrides.pop(get_db, None)
        _active_agents.pop(call_id, None)

    assert isinstance(data["content"], str)
    assert data["content_complete"] is True
