"""
Tests for /auth/* routes.

All tests use an in-memory SQLite database via the `db` fixture from conftest.py,
and an httpx.AsyncClient pointed at the FastAPI app with get_db overridden.
"""
from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app
from app.database import get_db
from app.models.contractor import Contractor
from app.utils.auth import hash_password
from app.utils.sessions import SESSION_COOKIE
from app.routers.auth import _issue_reset_token


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_contractor(email: str = "contractor@example.com", password: str = "Secur3Pass!") -> Contractor:
    return Contractor(
        id=uuid.uuid4(),
        name="Test Plumbing Co",
        agent_name="Alex",
        phone_number="+15550009999",
        api_key=secrets.token_hex(32),
        trades=["plumbing"],
        service_areas=["Calgary"],
        hashed_password=hash_password(password),
        email=email,
        is_active=True,
        is_verified=True,
        plan="starter",
        calls_this_month=0,
        sms_this_month=0,
        calendar_provider="manual",
        calendar_config={},
        sms_enabled=True,
        diagnostic_fee=89.0,
        free_estimate=False,
    )


@pytest_asyncio.fixture
async def seeded_db(db: AsyncSession):
    """Return a db session that already contains one contractor."""
    contractor = _make_contractor()
    db.add(contractor)
    await db.commit()
    return db, contractor


def _override(db_session: AsyncSession):
    """Return a FastAPI dependency override that yields the test session."""
    async def _dep():
        yield db_session
    return _dep


# ---------------------------------------------------------------------------
# 1. GET /auth/login → 200
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_login_get_returns_200(db: AsyncSession):
    app.dependency_overrides[get_db] = _override(db)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/auth/login")
        assert resp.status_code == 200
    finally:
        app.dependency_overrides.pop(get_db, None)


# ---------------------------------------------------------------------------
# 2. POST /auth/login with valid credentials → 302 redirect to /portal
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_login_post_valid_credentials_redirects(seeded_db):
    db, _ = seeded_db
    app.dependency_overrides[get_db] = _override(db)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", follow_redirects=False) as client:
            resp = await client.post(
                "/auth/login",
                data={"email": "contractor@example.com", "password": "Secur3Pass!"},
            )
        assert resp.status_code == 302
        assert "/portal" in resp.headers["location"]
        assert SESSION_COOKIE in resp.cookies
    finally:
        app.dependency_overrides.pop(get_db, None)


# ---------------------------------------------------------------------------
# 3. POST /auth/login with wrong password → 200 with error (not redirect)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_login_post_wrong_password_returns_error(seeded_db):
    db, _ = seeded_db
    app.dependency_overrides[get_db] = _override(db)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", follow_redirects=False) as client:
            resp = await client.post(
                "/auth/login",
                data={"email": "contractor@example.com", "password": "WrongPassword!"},
                headers={"x-forwarded-for": "10.0.0.1"},
            )
        assert resp.status_code == 401
        assert SESSION_COOKIE not in resp.cookies
    finally:
        app.dependency_overrides.pop(get_db, None)


# ---------------------------------------------------------------------------
# 4. POST /auth/login with unknown email → 200 with error
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_login_post_unknown_email_returns_error(db: AsyncSession):
    app.dependency_overrides[get_db] = _override(db)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", follow_redirects=False) as client:
            resp = await client.post(
                "/auth/login",
                data={"email": "nobody@nowhere.com", "password": "SomePassword1!"},
                headers={"x-forwarded-for": "10.0.0.2"},
            )
        assert resp.status_code == 401
        assert SESSION_COOKIE not in resp.cookies
    finally:
        app.dependency_overrides.pop(get_db, None)


# ---------------------------------------------------------------------------
# 5. GET /auth/logout → 302 redirect, SESSION_COOKIE deleted
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_logout_redirects_and_clears_cookie(db: AsyncSession):
    app.dependency_overrides[get_db] = _override(db)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", follow_redirects=False) as client:
            resp = await client.get("/auth/logout")
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers["location"]
        # Cookie should be cleared (set to empty or max-age=0)
        set_cookie_header = resp.headers.get("set-cookie", "")
        assert SESSION_COOKIE in set_cookie_header
    finally:
        app.dependency_overrides.pop(get_db, None)


# ---------------------------------------------------------------------------
# 6. POST /auth/forgot-password with known email → 200 (always shows success)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_forgot_password_known_email_shows_success(seeded_db):
    db, _ = seeded_db
    app.dependency_overrides[get_db] = _override(db)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", follow_redirects=False) as client:
            resp = await client.post(
                "/auth/forgot-password",
                data={"email": "contractor@example.com"},
                headers={"x-forwarded-for": "10.0.1.1"},
            )
        assert resp.status_code == 200
    finally:
        app.dependency_overrides.pop(get_db, None)


# ---------------------------------------------------------------------------
# 7. POST /auth/forgot-password with unknown email → 200 (no info leak)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_forgot_password_unknown_email_shows_same_response(db: AsyncSession):
    app.dependency_overrides[get_db] = _override(db)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", follow_redirects=False) as client:
            resp = await client.post(
                "/auth/forgot-password",
                data={"email": "nosuchuser@example.com"},
                headers={"x-forwarded-for": "10.0.1.2"},
            )
        assert resp.status_code == 200
    finally:
        app.dependency_overrides.pop(get_db, None)


# ---------------------------------------------------------------------------
# 8. POST /auth/reset-password with valid token → 302 redirect to login
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reset_password_valid_token_redirects_to_login(seeded_db):
    db, contractor = seeded_db
    # Issue a real reset token via the internal helper
    raw_token = await _issue_reset_token(contractor, db)
    await db.commit()

    app.dependency_overrides[get_db] = _override(db)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", follow_redirects=False) as client:
            resp = await client.post(
                "/auth/reset-password",
                data={
                    "email": contractor.email,
                    "token": raw_token,
                    "new_password": "NewSecure123!",
                    "confirm_password": "NewSecure123!",
                },
            )
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers["location"]
    finally:
        app.dependency_overrides.pop(get_db, None)


# ---------------------------------------------------------------------------
# 9. POST /auth/reset-password with expired token → 400 with error
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reset_password_expired_token_returns_400(seeded_db):
    db, contractor = seeded_db
    # Set an expired token directly on the contractor
    contractor.reset_token = secrets.token_urlsafe(48)
    contractor.reset_token_expires_at = datetime.now(tz=timezone.utc) - timedelta(hours=2)
    await db.commit()

    app.dependency_overrides[get_db] = _override(db)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", follow_redirects=False) as client:
            resp = await client.post(
                "/auth/reset-password",
                data={
                    "email": contractor.email,
                    "token": contractor.reset_token,
                    "new_password": "NewSecure123!",
                    "confirm_password": "NewSecure123!",
                },
            )
        assert resp.status_code == 400
    finally:
        app.dependency_overrides.pop(get_db, None)


# ---------------------------------------------------------------------------
# 10. POST /auth/reset-password with mismatched passwords → 400
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reset_password_mismatched_passwords_returns_400(seeded_db):
    db, contractor = seeded_db
    raw_token = await _issue_reset_token(contractor, db)
    await db.commit()

    app.dependency_overrides[get_db] = _override(db)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", follow_redirects=False) as client:
            resp = await client.post(
                "/auth/reset-password",
                data={
                    "email": contractor.email,
                    "token": raw_token,
                    "new_password": "NewSecure123!",
                    "confirm_password": "DifferentPassword456!",
                },
            )
        assert resp.status_code == 400
    finally:
        app.dependency_overrides.pop(get_db, None)


# ---------------------------------------------------------------------------
# 11. POST /auth/signup with valid data → 302 redirect (mock provisioning)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_signup_valid_data_redirects(db: AsyncSession):
    app.dependency_overrides[get_db] = _override(db)
    try:
        with patch("asyncio.create_task", side_effect=lambda c, *a, **k: c.close() or None):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", follow_redirects=False) as client:
                resp = await client.post(
                    "/auth/signup",
                    data={
                        "business_name": "Sunrise HVAC",
                        "email": "signup_test@example.com",
                        "password": "ValidPass99!",
                        "confirm_password": "ValidPass99!",
                        "trade": "hvac",
                        "phone": "+15550001111",
                        "service_area": "T3B",
                    },
                    headers={"x-forwarded-for": "10.0.2.1"},
                )
        assert resp.status_code == 302
        assert SESSION_COOKIE in resp.cookies
    finally:
        app.dependency_overrides.pop(get_db, None)


# ---------------------------------------------------------------------------
# 12. POST /auth/signup with duplicate email → 400 with error
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_signup_duplicate_email_returns_400(seeded_db):
    db, contractor = seeded_db
    app.dependency_overrides[get_db] = _override(db)
    try:
        with patch("asyncio.create_task", side_effect=lambda c, *a, **k: c.close() or None):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", follow_redirects=False) as client:
                resp = await client.post(
                    "/auth/signup",
                    data={
                        "business_name": "Dup Co",
                        "email": contractor.email,  # already in DB
                        "password": "ValidPass99!",
                        "confirm_password": "ValidPass99!",
                        "trade": "plumbing",
                        "phone": "+15550007777",
                        "service_area": "T2P",
                    },
                    headers={"x-forwarded-for": "10.0.2.2"},
                )
        assert resp.status_code == 400
    finally:
        app.dependency_overrides.pop(get_db, None)


# ---------------------------------------------------------------------------
# 13. POST /auth/signup with password mismatch → 400 with error
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_signup_password_mismatch_returns_400(db: AsyncSession):
    app.dependency_overrides[get_db] = _override(db)
    try:
        with patch("asyncio.create_task", side_effect=lambda c, *a, **k: c.close() or None):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", follow_redirects=False) as client:
                resp = await client.post(
                    "/auth/signup",
                    data={
                        "business_name": "Mismatch HVAC",
                        "email": "mismatch@example.com",
                        "password": "ValidPass99!",
                        "confirm_password": "WrongConfirm!",
                        "trade": "hvac",
                        "phone": "+15550008888",
                        "service_area": "T3C",
                    },
                    headers={"x-forwarded-for": "10.0.2.3"},
                )
        assert resp.status_code == 400
    finally:
        app.dependency_overrides.pop(get_db, None)
