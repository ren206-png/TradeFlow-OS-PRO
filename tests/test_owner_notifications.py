"""Owner alerts go to the owner's mobile, never the AI line; settings can't break call routing."""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy import select

from app.database import get_db
from app.main import app
from app.models.contractor import Contractor
from app.services.notifications import notify_new_lead, owner_alert_phone
from app.utils.phone import normalize_nanp
from app.utils.sessions import SESSION_COOKIE, create_session_token
from tests.test_auth import _make_contractor

AI_LINE = "+15875550199"


@pytest.mark.parametrize("raw,expected", [
    ("(403) 555-0123", "+14035550123"), ("4035550123", "+14035550123"),
    ("+1 403 555 0123", "+14035550123"), ("14035550123", "+14035550123"),
    ("555-0123", None), ("", None),
])
def test_normalize_nanp(raw, expected):
    assert normalize_nanp(raw) == expected


def test_owner_alert_phone_never_uses_ai_line():
    c = SimpleNamespace(phone_number=AI_LINE, owner_phone=None, calendar_config={})
    assert owner_alert_phone(c) is None
    c.calendar_config = {"transfer_number": "+14035550111"}
    assert owner_alert_phone(c) == "+14035550111"
    c.owner_phone = "+14035550122"
    assert owner_alert_phone(c) == "+14035550122"


@pytest.mark.asyncio
async def test_new_lead_text_goes_to_owner_mobile():
    contractor = SimpleNamespace(id=uuid.uuid4(), name="Summit", phone_number=AI_LINE, owner_phone="+14035550122",
                                 calendar_config={}, email=None)
    lead = SimpleNamespace(id=uuid.uuid4(), caller_name="Jamie", phone="+18075550000", trade="plumbing",
                           problem_summary="Kitchen leak", appointment_status="new", priority_level="high",
                           service_address="1 Main St", city="Calgary")
    with patch("app.services.sms.SMSService._send_async", new_callable=AsyncMock) as send:
        await notify_new_lead(contractor, lead)
    assert send.await_args.args[0] == "+14035550122"


async def _post_settings(db, contractor, data):
    async def _dep():
        yield db
    app.dependency_overrides[get_db] = _dep
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                     cookies={SESSION_COOKIE: create_session_token(str(contractor.id))}) as c:
            return await c.post("/portal/settings/update", data=data)
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_settings_save_owner_and_transfer_but_not_ai_line(db):
    contractor = _make_contractor(email="owner@example.com")
    contractor.phone_number = AI_LINE
    db.add(contractor)
    await db.commit()

    resp = await _post_settings(db, contractor, {
        "name": contractor.name, "phone_number": "+14035550000",
        "owner_phone": "(403) 555-0122", "transfer_number": "403.555.0111",
    })
    assert resp.status_code == 302
    await db.refresh(contractor)
    assert contractor.phone_number == AI_LINE  # ignored: editing it would break call routing
    assert contractor.owner_phone == "+14035550122"
    assert contractor.calendar_config["transfer_number"] == "+14035550111"

    await _post_settings(db, contractor, {"name": contractor.name, "owner_phone": "", "transfer_number": ""})
    await db.refresh(contractor)
    assert contractor.owner_phone is None
    assert "transfer_number" not in contractor.calendar_config


@pytest.mark.asyncio
async def test_signup_keeps_owner_phone(db):
    async def _dep():
        yield db
    app.dependency_overrides[get_db] = _dep
    try:
        with patch("asyncio.create_task", side_effect=lambda c, *a, **k: c.close() or None):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
                await c.post("/onboarding", data={
                    "company_name": "Owner Test", "agent_name": "Sam", "email": "ownertest@example.com",
                    "password": "Password123", "confirm_password": "Password123", "phone_number": "(403) 555-0177",
                }, headers={"x-forwarded-for": "9.9.9.77"})
    finally:
        app.dependency_overrides.clear()
    row = (await db.execute(select(Contractor).where(Contractor.email == "ownertest@example.com"))).scalar_one()
    assert row.owner_phone == "+14035550177"


@pytest.mark.asyncio
async def test_settings_page_shows_ai_line_read_only(db):
    contractor = _make_contractor(email="render@example.com")
    contractor.phone_number, contractor.owner_phone = AI_LINE, "+14035550122"
    db.add(contractor)
    await db.commit()

    async def _dep():
        yield db
    app.dependency_overrides[get_db] = _dep
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                     cookies={SESSION_COOKIE: create_session_token(str(contractor.id))}) as c:
            html = (await c.get("/portal/settings")).text
    finally:
        app.dependency_overrides.clear()
    assert 'name="owner_phone"' in html and 'name="transfer_number"' in html
    assert 'name="phone_number"' not in html
    assert "AI receptionist number" in html
