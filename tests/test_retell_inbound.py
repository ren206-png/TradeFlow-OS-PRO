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
