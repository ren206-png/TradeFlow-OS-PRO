"""Telnyx SMS provider, inbound webhook signature, and shared-sender reply routing."""
from __future__ import annotations

import base64
import json
import time
import uuid
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from app.config import settings
from app.database import get_db
from app.main import app
from app.models.lead import Lead
from app.models.outbound_ledger import OutboundLedger
from app.routers.twilio_sms import _resolve_tenant_from_to
from app.services import sms_provider
from tests.test_auth import _make_contractor


@pytest.fixture
def telnyx_settings(monkeypatch):
    key = Ed25519PrivateKey.generate()
    pub = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    monkeypatch.setattr(settings, "sms_provider", "telnyx")
    monkeypatch.setattr(settings, "telnyx_api_key", "KEY_test")
    monkeypatch.setattr(settings, "telnyx_messaging_profile_id", "profile-1")
    monkeypatch.setattr(settings, "telnyx_from_number", "+15870000000")
    monkeypatch.setattr(settings, "telnyx_public_key", base64.b64encode(pub).decode())
    return key


def _signed(key, body: bytes, ts: int | None = None) -> dict:
    ts = str(ts if ts is not None else int(time.time()))
    sig = key.sign(ts.encode() + b"|" + body)
    return {"telnyx-signature-ed25519": base64.b64encode(sig).decode(), "telnyx-timestamp": ts,
            "content-type": "application/json"}


def _event(text: str, frm: str = "+15875550123", to: str = "+15870000000") -> bytes:
    return json.dumps({"data": {"event_type": "message.received", "payload": {
        "from": {"phone_number": frm}, "to": [{"phone_number": to}], "text": text}}}).encode()


@pytest.mark.asyncio
async def test_telnyx_send_builds_correct_request(telnyx_settings):
    captured = {}

    async def fake_post(self, url, **kwargs):
        captured["url"], captured["kwargs"] = url, kwargs
        return httpx.Response(200, json={"data": {"id": "msg-123"}}, request=httpx.Request("POST", url))

    with patch.object(httpx.AsyncClient, "post", fake_post):
        result = await sms_provider.send_sms("+15875550123", "hello", "test")

    assert result == {"success": True, "sid": "msg-123"}
    assert captured["url"] == "https://api.telnyx.com/v2/messages"
    assert captured["kwargs"]["json"] == {"to": "+15875550123", "text": "hello",
                                          "from": "+15870000000", "messaging_profile_id": "profile-1"}
    assert captured["kwargs"]["headers"]["Authorization"] == "Bearer KEY_test"


@pytest.mark.asyncio
async def test_twilio_remains_default(monkeypatch):
    monkeypatch.setattr(settings, "sms_provider", "twilio")
    monkeypatch.setattr(settings, "twilio_account_sid", "AC1")
    monkeypatch.setattr(settings, "twilio_auth_token", "tok")
    captured = {}

    async def fake_post(self, url, **kwargs):
        captured["url"] = url
        return httpx.Response(201, json={"sid": "SM1"}, request=httpx.Request("POST", url))

    with patch.object(httpx.AsyncClient, "post", fake_post):
        result = await sms_provider.send_sms("+15875550123", "hi")
    assert result["sid"] == "SM1" and "api.twilio.com" in captured["url"]


@pytest.mark.asyncio
async def test_unconfigured_provider_skips(monkeypatch):
    monkeypatch.setattr(settings, "sms_provider", "telnyx")
    monkeypatch.setattr(settings, "telnyx_api_key", "")
    assert (await sms_provider.send_sms("+1", "x"))["success"] is False


@pytest.mark.asyncio
async def test_webhook_rejects_bad_and_stale_signatures(db, telnyx_settings):
    async def _dep():
        yield db
    app.dependency_overrides[get_db] = _dep
    body = _event("HELP")
    other = Ed25519PrivateKey.generate()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            forged = await c.post("/telnyx/sms", content=body, headers=_signed(other, body))
            stale = await c.post("/telnyx/sms", content=body,
                                 headers=_signed(telnyx_settings, body, int(time.time()) - 3600))
            missing = await c.post("/telnyx/sms", content=body, headers={"content-type": "application/json"})
    finally:
        app.dependency_overrides.clear()
    assert forged.status_code == stale.status_code == missing.status_code == 403


@pytest.mark.asyncio
async def test_stop_is_recorded_but_not_double_replied(db, telnyx_settings):
    """Telnyx auto-replies to STOP; the app must record the opt-out without sending its own reply."""
    from app.services.sms_compliance import is_opted_out

    async def _dep():
        yield db
    app.dependency_overrides[get_db] = _dep
    try:
        with patch("app.routers.telnyx_sms.send_sms", new_callable=AsyncMock) as reply:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
                for word in ("STOP", "info"):
                    body = _event(word)
                    resp = await c.post("/telnyx/sms", content=body, headers=_signed(telnyx_settings, body))
                    assert resp.status_code == 200
    finally:
        app.dependency_overrides.clear()
    assert await is_opted_out("+15875550123", db)
    # STOP: no app reply. INFO isn't a Telnyx profile keyword, so the app answers it.
    assert reply.await_count == 1
    assert reply.await_args.args[0] == "+15875550123"


@pytest.mark.asyncio
async def test_reply_to_shared_sender_resolves_contractor(db):
    """Texts go out from a shared number, so replies must map back via outbound history or leads."""
    via_ledger = _make_contractor(email="a@example.com")
    via_lead = _make_contractor(email="b@example.com")
    via_lead.phone_number = "+15550002222"
    db.add_all([via_ledger, via_lead])
    db.add(OutboundLedger(tenant_id=str(via_ledger.id), idempotency_key=uuid.uuid4().hex,
                          recipient_phone="+15875550001", channel="sms", status="sent"))
    db.add(Lead(contractor_id=via_lead.id, call_id="c1", phone="+15875550002"))
    await db.commit()

    c1, _ = await _resolve_tenant_from_to("+15870000000", db, "+15875550001")
    c2, _ = await _resolve_tenant_from_to("+15870000000", db, "+15875550002")
    c3, _ = await _resolve_tenant_from_to("+15870000000", db, "+15875559999")
    assert c1.id == via_ledger.id
    assert c2.id == via_lead.id
    assert c3 is None


@pytest.mark.asyncio
async def test_twilio_webhook_fails_closed_without_token(db, monkeypatch):
    monkeypatch.setattr(settings, "twilio_auth_token", "")

    async def _dep():
        yield db
    app.dependency_overrides[get_db] = _dep
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/twilio/sms", data={"From": "+15875550123", "Body": "CALL", "To": "+15870000000"})
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 503
