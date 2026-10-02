"""/retell/inbound routing: Retell's nested call_inbound payload, response format, signature handling."""
from __future__ import annotations

import hashlib
import hmac
import json
import time

import httpx
import pytest

from app.config import settings
from app.database import get_db
from app.main import app
from tests.test_auth import _make_contractor


async def _seed(db):
    demo = _make_contractor(email="demo@example.com")
    demo.phone_number, demo.retell_agent_id = "+15875550101", "agent_demo"
    other = _make_contractor(email="other@example.com")
    other.phone_number, other.retell_agent_id = "+15875550102", "agent_other"
    db.add_all([other, demo])
    await db.commit()
    return demo, other


def _sign(body: bytes) -> dict:
    ts = str(int(time.time() * 1000))
    digest = hmac.new(settings.retell_api_key.encode(), body + ts.encode(), hashlib.sha256).hexdigest()
    return {"x-retell-signature": f"v={ts},d={digest}", "content-type": "application/json"}


async def _post(db, body: bytes, headers: dict):
    async def _dep():
        yield db
    app.dependency_overrides[get_db] = _dep
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            return await c.post("/retell/inbound", content=body, headers=headers)
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_nested_call_inbound_payload_routes_to_dialed_contractor(db):
    await _seed(db)
    body = json.dumps({"event": "call_inbound", "call_inbound": {
        "agent_id": "agent_unrelated", "from_number": "+18075550000", "to_number": "+15875550101"}}).encode()
    resp = await _post(db, body, _sign(body))
    assert resp.status_code == 200
    data = resp.json()
    assert data["call_inbound"]["override_agent_id"] == "agent_demo"
    assert data["agent_id"] == "agent_demo"


@pytest.mark.asyncio
async def test_flat_payload_still_supported(db):
    await _seed(db)
    body = json.dumps({"to_number": "+15875550102", "from_number": "+18075550000"}).encode()
    resp = await _post(db, body, _sign(body))
    assert resp.json()["call_inbound"]["override_agent_id"] == "agent_other"


@pytest.mark.asyncio
async def test_unsigned_request_allowed_until_enforcement_enabled(db, monkeypatch):
    await _seed(db)
    body = json.dumps({"call_inbound": {"to_number": "+15875550101"}}).encode()
    monkeypatch.setattr(settings, "retell_inbound_enforce_signature", False)
    assert (await _post(db, body, {"content-type": "application/json"})).status_code == 200
    monkeypatch.setattr(settings, "retell_inbound_enforce_signature", True)
    assert (await _post(db, body, {"content-type": "application/json"})).status_code == 403
    assert (await _post(db, body, _sign(body))).status_code == 200


def test_llm_websocket_accepts_templated_agent_url():
    """Agents configured with '/llm-websocket/{call_id}' get the real id appended after the literal."""
    from fastapi.routing import APIWebSocketRoute
    paths = {r.path for r in app.routes if isinstance(r, APIWebSocketRoute)}
    assert "/llm-websocket/{call_id}" in paths
    assert "/llm-websocket/{url_template}/{call_id}" in paths


@pytest.mark.asyncio
@pytest.mark.parametrize("call_id,status_code,call_status,expected", [
    ("call_abc123", 200, "ongoing", True),
    ("call_abc123", 200, "registered", True),
    ("call_abc123", 200, "ended", False),
    ("call_abc123", 404, None, False),
    ("not-a-call-id", 200, "ongoing", False),
])
async def test_is_live_retell_call(call_id, status_code, call_status, expected):
    from unittest.mock import patch
    from app.routers import retell

    async def fake_get(self, url, **kwargs):
        return httpx.Response(status_code, json={"call_status": call_status}, request=httpx.Request("GET", url))

    with patch.object(httpx.AsyncClient, "get", fake_get):
        assert await retell._is_live_retell_call(call_id) is expected


def test_websocket_rejects_unverified_call():
    from unittest.mock import AsyncMock, patch
    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    with patch("app.routers.retell._is_live_retell_call", new=AsyncMock(return_value=False)):
        with pytest.raises(WebSocketDisconnect):
            with TestClient(app).websocket_connect("/llm-websocket/call_forged") as ws:
                ws.receive_json()


@pytest.mark.asyncio
async def test_unknown_number_is_rejected_not_routed_to_another_tenant(db):
    await _seed(db)
    body = json.dumps({"call_inbound": {"to_number": "+15875559999", "from_number": "+18075550000"}}).encode()
    resp = await _post(db, body, _sign(body))
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_signature_verified_with_webhook_secret(db, monkeypatch):
    await _seed(db)
    monkeypatch.setattr(settings, "retell_webhook_secret", "key_webhookbadge123")
    monkeypatch.setattr(settings, "retell_inbound_enforce_signature", True)
    body = json.dumps({"call_inbound": {"to_number": "+15875550101"}}).encode()
    ts = str(int(time.time() * 1000))
    digest = hmac.new(b"key_webhookbadge123", body + ts.encode(), hashlib.sha256).hexdigest()
    headers = {"x-retell-signature": f"v={ts},d={digest}", "content-type": "application/json"}
    assert (await _post(db, body, headers)).status_code == 200


async def _call_ended(db, call_id: str, seconds: int, lead_id=None, call_status: str = "ended"):
    from app.models.call import CallSession
    demo, _ = await _seed(db)
    db.add(CallSession(retell_call_id=call_id, contractor_id=demo.id, status="active",
                       conversation_history=[], lead_id=lead_id))
    await db.commit()
    start = 1_790_000_000_000
    body = json.dumps({"event": "call_ended", "call": {
        "call_id": call_id, "direction": "inbound", "from_number": "+18075550000",
        "to_number": "+15875550101", "call_status": call_status,
        "start_timestamp": start, "end_timestamp": start + seconds * 1000}}).encode()

    async def _dep():
        yield db
    app.dependency_overrides[get_db] = _dep
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/retell/webhook", content=body, headers=_sign(body))
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200
    return demo


@pytest.mark.asyncio
async def test_conversation_without_lead_becomes_callback_lead(db):
    from unittest.mock import AsyncMock, patch
    from sqlalchemy import select
    from app.models.lead import Lead
    with patch("app.services.notifications.notify_new_lead", new_callable=AsyncMock) as notify:
        demo = await _call_ended(db, "call_partial1", seconds=40)
    notify.assert_called_once()
    lead = (await db.execute(select(Lead).where(Lead.call_id == "call_partial1"))).scalar_one()
    assert lead.contractor_id == demo.id
    assert lead.phone == "+18075550000"
    assert lead.lead_source == "retell_partial_call"
    assert lead.appointment_status == "callback_required"


@pytest.mark.asyncio
async def test_very_short_call_does_not_create_lead(db):
    from sqlalchemy import select
    from app.models.lead import Lead
    await _call_ended(db, "call_short1", seconds=4)
    assert (await db.execute(select(Lead).where(Lead.call_id == "call_short1"))).first() is None


@pytest.mark.asyncio
async def test_call_analyzed_first_still_gets_summary(db):
    """Retell can send call_analyzed before call_ended; the summary must land on the lead."""
    from sqlalchemy import select
    from app.models.call import CallSession
    from app.models.lead import Lead

    demo, _ = await _seed(db)
    db.add(CallSession(retell_call_id="call_order1", contractor_id=demo.id, status="active", conversation_history=[]))
    await db.commit()
    start = 1_790_000_000_000
    body = json.dumps({"event": "call_analyzed", "call": {
        "call_id": "call_order1", "direction": "inbound", "from_number": "+18075550000",
        "to_number": "+15875550101", "start_timestamp": start, "end_timestamp": start + 30_000,
        "call_analysis": {"call_summary": "Active kitchen leak; wants a plumber.", "user_sentiment": "Neutral"}}}).encode()

    async def _dep():
        yield db
    app.dependency_overrides[get_db] = _dep
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            assert (await c.post("/retell/webhook", content=body, headers=_sign(body))).status_code == 200
    finally:
        app.dependency_overrides.clear()
    lead = (await db.execute(select(Lead).where(Lead.call_id == "call_order1"))).scalar_one()
    assert lead.ai_summary == "Active kitchen leak; wants a plumber."


@pytest.mark.asyncio
async def test_concurrent_websocket_connections_share_one_call_session(db):
    """Retell reconnects mid-call; every connection must land on the same, already-committed row."""
    from sqlalchemy import func, select
    from sqlalchemy.exc import IntegrityError
    from unittest.mock import patch
    from app.models.call import CallSession
    from app.routers.retell import _get_or_create_call_session

    demo, _ = await _seed(db)
    first = await _get_or_create_call_session("call_race1", demo.id, db)
    again = await _get_or_create_call_session("call_race1", demo.id, db)
    assert again.id == first.id

    # Race: another connection commits the same call's row between our check and our commit.
    from sqlalchemy.ext.asyncio import AsyncSession
    real_commit = db.commit
    state = {"raised": False}

    async def commit_with_race():
        if not state["raised"]:
            state["raised"] = True
            async with AsyncSession(db.bind, expire_on_commit=False) as other:
                other.add(CallSession(retell_call_id="call_race2", contractor_id=demo.id, status="active",
                                      conversation_history=[]))
                await other.commit()
            raise IntegrityError("insert", {}, Exception("duplicate retell_call_id"))
        await real_commit()

    with patch.object(db, "commit", commit_with_race):
        winner = await _get_or_create_call_session("call_race2", demo.id, db)
    assert winner.retell_call_id == "call_race2" and state["raised"]
    count = (await db.execute(select(func.count()).select_from(CallSession).where(
        CallSession.retell_call_id == "call_race2"))).scalar_one()
    assert count == 1


def test_websocket_turn_commits_each_turn():
    """Source-level guard: the turn handler persists after every turn (see commit comment)."""
    import inspect
    from app.routers import retell
    src = inspect.getsource(retell.llm_websocket)
    assert src.count("await db.commit()") >= 2


def _ws_client_with_agent(process_turn):
    """Open the LLM WebSocket against a fake agent whose turn handler is `process_turn`."""
    from unittest.mock import AsyncMock, MagicMock, patch
    from starlette.testclient import TestClient
    from app.database import get_db

    agent = MagicMock()
    agent.process_turn = process_turn
    agent.call_session = MagicMock(lead_id=None, retell_call_id="call_hb1")
    db = AsyncMock()

    async def _dep():
        yield db

    app.dependency_overrides[get_db] = _dep
    patches = [
        patch("app.routers.retell._is_live_retell_call", new=AsyncMock(return_value=True)),
        patch("app.routers.retell._rebuild_agent", new=AsyncMock(return_value=agent)),
        patch("app.routers.retell._should_end_call", return_value=False),
        patch("app.routers.retell.broadcast_call_event", new=AsyncMock()),
    ]
    return TestClient(app), db, patches


def test_heartbeat_is_answered_while_a_slow_turn_runs():
    """Retell drops the connection if ping_pong isn't answered within 5s; turns take 10s+."""
    import asyncio
    from contextlib import ExitStack

    async def slow_turn(_msg):
        await asyncio.sleep(1.0)
        return "Thanks, one moment while I check."

    client, db, patches = _ws_client_with_agent(slow_turn)
    try:
        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            with client.websocket_connect("/llm-websocket/call_hb1") as ws:
                ws.receive_json()  # config
                ws.send_json({"interaction_type": "response_required", "response_id": 1,
                              "transcript": [{"role": "user", "content": "my faucet drips"}]})
                ws.send_json({"interaction_type": "ping_pong", "timestamp": 12345})
                first = ws.receive_json()
                second = ws.receive_json()
    finally:
        app.dependency_overrides.clear()
    assert first == {"response_type": "ping_pong", "timestamp": 12345}   # answered immediately
    assert second["response_type"] == "response" and second["response_id"] == 1


def test_superseded_turn_response_is_not_sent():
    """If the caller speaks again while we're thinking, the older answer must not be spoken."""
    import asyncio
    from contextlib import ExitStack

    calls = []

    async def turn(msg):
        calls.append(msg)
        await asyncio.sleep(0.6)
        return f"answer to {msg}"

    client, db, patches = _ws_client_with_agent(turn)
    try:
        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            with client.websocket_connect("/llm-websocket/call_hb1") as ws:
                ws.receive_json()
                ws.send_json({"interaction_type": "response_required", "response_id": 1,
                              "transcript": [{"role": "user", "content": "first"}]})
                ws.send_json({"interaction_type": "response_required", "response_id": 2,
                              "transcript": [{"role": "user", "content": "second"}]})
                reply = ws.receive_json()
    finally:
        app.dependency_overrides.clear()
    assert reply["response_id"] == 2 and reply["content"] == "answer to second"


def test_disconnect_does_not_end_the_call():
    """A dropped/rotated connection is not a hang-up; only the call_ended webhook finalises."""
    from contextlib import ExitStack
    from unittest.mock import AsyncMock, patch

    client, db, patches = _ws_client_with_agent(AsyncMock(return_value="hi"))
    try:
        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            fin = stack.enter_context(patch("app.routers.retell._finalise_session", new=AsyncMock()))
            with client.websocket_connect("/llm-websocket/call_hb1") as ws:
                ws.receive_json()
    finally:
        app.dependency_overrides.clear()
    fin.assert_not_called()


@pytest.mark.asyncio
async def test_errored_call_after_real_conversation_gets_no_missed_call_text(db):
    """A call that died after 40s is a lost conversation (callback lead), not a 'sorry we missed you'."""
    from unittest.mock import AsyncMock, patch
    with patch("app.services.notifications.notify_new_lead", new_callable=AsyncMock), \
         patch("app.services.missed_call.send_missed_call_sms", new_callable=AsyncMock) as missed:
        await _call_ended(db, "call_err40", seconds=40, call_status="error")
    missed.assert_not_called()
