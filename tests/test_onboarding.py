"""
Tests for /onboarding routes.

All tests use an in-memory SQLite database via the `db` fixture from conftest.py,
and an httpx.AsyncClient pointed at the FastAPI app with get_db overridden.
"""
from __future__ import annotations

import secrets
import uuid
from unittest.mock import patch

import pytest
import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app
from app.database import get_db
from app.models.contractor import Contractor
from app.utils.auth import hash_password
from app.utils.rate_limit import _windows  # to reset rate-limiter state between tests
from app.utils.sessions import SESSION_COOKIE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _override(db_session: AsyncSession):
    async def _dep():
        yield db_session
    return _dep


def _clear_rate_limit_for(ip: str, action: str = "onboarding"):
    """Remove any recorded rate-limit entries for a given IP+action key."""
    key = f"{ip}:{action}"
    if key in _windows:
        _windows[key].clear()


VALID_FORM = {
    "company_name": "Sunrise Plumbing",
    "agent_name": "Bob Smith",
    "email": "bob@sunriseplumbing.com",
    "password": "SecurePass1!",
    "confirm_password": "SecurePass1!",
    "phone_number": "+15550003333",
    "service_areas": "T2N, T2P",
    "trades": "Plumbing",
}


# ---------------------------------------------------------------------------
# 1. GET /onboarding → 200
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_onboarding_get_returns_200(db: AsyncSession):
    app.dependency_overrides[get_db] = _override(db)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/onboarding")
        assert resp.status_code == 200
    finally:
        app.dependency_overrides.pop(get_db, None)


# ---------------------------------------------------------------------------
# 2. POST /onboarding with complete valid data → 303 redirect to /portal/leads?welcome=1
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_onboarding_post_valid_redirects(db: AsyncSession):
    ip = "20.0.0.1"
    _clear_rate_limit_for(ip)
    app.dependency_overrides[get_db] = _override(db)
    try:
        with patch("asyncio.create_task", side_effect=lambda c, *a, **k: c.close() or None):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", follow_redirects=False) as client:
                resp = await client.post(
                    "/onboarding",
                    data=VALID_FORM,
                    headers={"x-forwarded-for": ip},
                )
        assert resp.status_code == 303
        assert "/portal/leads" in resp.headers["location"]
        assert "welcome=1" in resp.headers["location"]
        assert SESSION_COOKIE in resp.cookies
    finally:
        app.dependency_overrides.pop(get_db, None)
        _clear_rate_limit_for(ip)


# ---------------------------------------------------------------------------
# 3. POST /onboarding missing required fields → 422
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_onboarding_post_missing_fields_returns_422(db: AsyncSession):
    ip = "20.0.0.2"
    _clear_rate_limit_for(ip)
    app.dependency_overrides[get_db] = _override(db)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", follow_redirects=False) as client:
            resp = await client.post(
                "/onboarding",
                # All required fields missing
                data={},
                headers={"x-forwarded-for": ip},
            )
        assert resp.status_code == 422
    finally:
        app.dependency_overrides.pop(get_db, None)
        _clear_rate_limit_for(ip)


# ---------------------------------------------------------------------------
# 4. POST /onboarding with password too short → 422
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_onboarding_post_short_password_returns_422(db: AsyncSession):
    ip = "20.0.0.3"
    _clear_rate_limit_for(ip)
    app.dependency_overrides[get_db] = _override(db)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", follow_redirects=False) as client:
            resp = await client.post(
                "/onboarding",
                data={
                    **VALID_FORM,
                    "email": "short@example.com",
                    "password": "abc",
                    "confirm_password": "abc",
                },
                headers={"x-forwarded-for": ip},
            )
        assert resp.status_code == 422
    finally:
        app.dependency_overrides.pop(get_db, None)
        _clear_rate_limit_for(ip)


# ---------------------------------------------------------------------------
# 5. POST /onboarding password mismatch → 422
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_onboarding_post_password_mismatch_returns_422(db: AsyncSession):
    ip = "20.0.0.4"
    _clear_rate_limit_for(ip)
    app.dependency_overrides[get_db] = _override(db)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", follow_redirects=False) as client:
            resp = await client.post(
                "/onboarding",
                data={
                    **VALID_FORM,
                    "email": "mismatch@example.com",
                    "password": "SecurePass1!",
                    "confirm_password": "DifferentPass2!",
                },
                headers={"x-forwarded-for": ip},
            )
        assert resp.status_code == 422
    finally:
        app.dependency_overrides.pop(get_db, None)
        _clear_rate_limit_for(ip)


# ---------------------------------------------------------------------------
# 6. POST /onboarding duplicate email → 422
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_onboarding_post_duplicate_email_returns_422(db: AsyncSession):
    ip = "20.0.0.5"
    _clear_rate_limit_for(ip)

    # Seed a contractor with the same email
    existing = Contractor(
        id=uuid.uuid4(),
        name="Existing Co",
        agent_name="Alice",
        phone_number="+15550006666",
        api_key=secrets.token_hex(32),
        trades=["plumbing"],
        service_areas=["Calgary"],
        hashed_password=hash_password("SomePassword1!"),
        email="bob@sunriseplumbing.com",  # same as VALID_FORM email (lowercased)
        is_active=True,
        calendar_provider="manual",
        calendar_config={},
    )
    db.add(existing)
    await db.commit()

    app.dependency_overrides[get_db] = _override(db)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", follow_redirects=False) as client:
            resp = await client.post(
                "/onboarding",
                data=VALID_FORM,
                headers={"x-forwarded-for": ip},
            )
        assert resp.status_code == 422
    finally:
        app.dependency_overrides.pop(get_db, None)
        _clear_rate_limit_for(ip)


# ---------------------------------------------------------------------------
# 7. Rate limit: after 5 submissions from same IP, 6th returns 429
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_onboarding_rate_limit_returns_429_after_5(db: AsyncSession):
    ip = "20.0.1.1"
    _clear_rate_limit_for(ip)
    app.dependency_overrides[get_db] = _override(db)
    try:
        with patch("asyncio.create_task", side_effect=lambda c, *a, **k: c.close() or None):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", follow_redirects=False) as client:
                # Submit 5 requests — each uses a unique email to avoid duplicate-email 422
                for i in range(5):
                    await client.post(
                        "/onboarding",
                        data={
                            **VALID_FORM,
                            "email": f"ratelimit{i}@example.com",
                            "phone_number": f"+1555000{i:04d}",
                        },
                        headers={"x-forwarded-for": ip},
                    )

                # 6th submission from the same IP must be rate-limited
                resp = await client.post(
                    "/onboarding",
                    data={
                        **VALID_FORM,
                        "email": "ratelimit6@example.com",
                        "phone_number": "+15550006000",
                    },
                    headers={"x-forwarded-for": ip},
                )

        assert resp.status_code == 429
    finally:
        app.dependency_overrides.pop(get_db, None)
        _clear_rate_limit_for(ip)
