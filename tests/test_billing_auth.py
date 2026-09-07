"""
Tests for /billing/* routes.

/billing/status and /billing/create-checkout are protected by X-API-Key header
via the get_contractor_from_api_key dependency (not a session cookie).

All tests use an in-memory SQLite database via the `db` fixture from conftest.py.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
import uuid
from unittest.mock import patch, AsyncMock

import pytest
import httpx
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app
from app.database import get_db
from app.models.contractor import Contractor
from app.utils.auth import get_contractor_from_api_key, hash_password
from app.routers.billing import _verify_stripe_signature


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _override(db_session: AsyncSession):
    async def _dep():
        yield db_session
    return _dep


def _make_contractor(api_key: str = "test-billing-key-abc") -> Contractor:
    return Contractor(
        id=uuid.uuid4(),
        name="Billing Test Co",
        agent_name="Alex",
        phone_number="+15550004444",
        api_key=api_key,
        trades=["plumbing"],
        service_areas=["Calgary"],
        hashed_password=hash_password("SomePass1!"),
        email="billing@example.com",
        is_active=True,
        is_verified=True,
        plan="starter",
        subscription_status="trial",
        calls_this_month=3,
        sms_this_month=1,
        calendar_provider="manual",
        calendar_config={},
        sms_enabled=True,
        diagnostic_fee=89.0,
        free_estimate=False,
    )


def _make_stripe_sig(payload: bytes, secret: str) -> str:
    """Produce a valid Stripe webhook signature header string."""
    ts = str(int(time.time()))
    signed_payload = f"{ts}.".encode() + payload
    sig = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


# ---------------------------------------------------------------------------
# 1. GET /billing/status without API key → 401 (missing auth header)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_billing_status_without_api_key_returns_401(db: AsyncSession):
    app.dependency_overrides[get_db] = _override(db)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", follow_redirects=False) as client:
            resp = await client.get("/billing/status")
        # Missing X-API-Key header → 401 Unauthorized
        assert resp.status_code == 401
    finally:
        app.dependency_overrides.pop(get_db, None)


# ---------------------------------------------------------------------------
# 2. GET /billing/status with valid API key → 200 with billing info
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_billing_status_with_valid_api_key_returns_200(db: AsyncSession):
    contractor = _make_contractor(api_key="valid-billing-key-001")
    db.add(contractor)
    await db.commit()

    app.dependency_overrides[get_db] = _override(db)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", follow_redirects=False) as client:
            resp = await client.get(
                "/billing/status",
                headers={"X-API-Key": "valid-billing-key-001"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["plan"] == "starter"
        assert body["subscription_status"] == "trial"
        assert body["calls_this_month"] == 3
        assert "calls_limit" in body
        assert "sms_limit" in body
    finally:
        app.dependency_overrides.pop(get_db, None)


# ---------------------------------------------------------------------------
# 3. POST /billing/webhook with invalid Stripe signature → 400
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_billing_webhook_invalid_signature_returns_400(db: AsyncSession):
    app.dependency_overrides[get_db] = _override(db)
    try:
        with patch("app.config.settings") as mock_settings:
            mock_settings.stripe_webhook_secret = "whsec_test_secret"
            mock_settings.stripe_secret_key = "sk_test_key"
            # Patch the settings reference used inside billing.py
            with patch("app.routers.billing.settings", mock_settings):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                    resp = await client.post(
                        "/billing/webhook",
                        content=b'{"type": "customer.subscription.updated"}',
                        headers={
                            "content-type": "application/json",
                            "stripe-signature": "t=12345,v1=invalidsignature",
                        },
                    )
        # Invalid sig → 403 (signature mismatch) or 400 (malformed header)
        assert resp.status_code in (400, 403)
    finally:
        app.dependency_overrides.pop(get_db, None)


# ---------------------------------------------------------------------------
# 4. POST /billing/create-checkout without API key → 401
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_billing_create_checkout_without_api_key_returns_401(db: AsyncSession):
    app.dependency_overrides[get_db] = _override(db)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", follow_redirects=False) as client:
            resp = await client.post(
                "/billing/create-checkout",
                json={"plan": "pro"},
            )
        # No X-API-Key header → 401
        assert resp.status_code == 401
    finally:
        app.dependency_overrides.pop(get_db, None)


# ---------------------------------------------------------------------------
# 5. _verify_stripe_signature raises HTTPException on tampered payload
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_verify_stripe_signature_raises_on_tampered_payload():
    """Unit-test the signature helper directly without going through the HTTP stack."""
    secret = "whsec_unittestsecret"
    real_payload = b'{"type": "invoice.payment_succeeded"}'
    tampered_payload = b'{"type": "invoice.payment_succeeded", "tampered": true}'

    # Build a valid signature for real_payload
    valid_sig = _make_stripe_sig(real_payload, secret)

    with patch("app.routers.billing.settings") as mock_settings:
        mock_settings.stripe_webhook_secret = secret

        # Valid payload + matching signature → should not raise
        try:
            _verify_stripe_signature(real_payload, valid_sig)
        except HTTPException:
            pytest.fail("_verify_stripe_signature raised unexpectedly on valid payload")

        # Tampered payload + same signature → must raise HTTPException (400 or 403)
        with pytest.raises(HTTPException) as exc_info:
            _verify_stripe_signature(tampered_payload, valid_sig)

        assert exc_info.value.status_code in (400, 403)


# ---------------------------------------------------------------------------
# 6. _verify_stripe_signature raises on malformed header
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_verify_stripe_signature_raises_on_malformed_header():
    """Malformed stripe-signature header (missing t= or v1=) must raise HTTPException."""
    secret = "whsec_malformedtest"
    payload = b'{"type": "test"}'
    malformed_sig = "no_timestamp_here,garbage"

    with patch("app.routers.billing.settings") as mock_settings:
        mock_settings.stripe_webhook_secret = secret

        with pytest.raises(HTTPException) as exc_info:
            _verify_stripe_signature(payload, malformed_sig)

        assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# 7. GET /billing/status with an invalid (unknown) API key → 401
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_billing_status_invalid_api_key_returns_401(db: AsyncSession):
    app.dependency_overrides[get_db] = _override(db)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", follow_redirects=False) as client:
            resp = await client.get(
                "/billing/status",
                headers={"X-API-Key": "completely-wrong-key"},
            )
        assert resp.status_code == 401
    finally:
        app.dependency_overrides.pop(get_db, None)
