"""Appointment times: slots in contractor-local hours, shown in local time, zone inferred at signup."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import httpx
import pytest
from sqlalchemy import select

from app.database import get_db
from app.main import app
from app.models.contractor import Contractor
from app.services.calendar import CalendarService
from app.utils.sessions import SESSION_COOKIE, create_session_token
from app.utils.timefmt import to_local, timezone_for_phone, zone
from tests.test_auth import _make_contractor


def test_to_local_converts_utc_and_treats_naive_as_utc():
    utc = datetime(2026, 10, 3, 16, 0, tzinfo=timezone.utc)
    assert to_local(utc, "America/Edmonton").strftime("%H:%M") == "10:00"      # MDT, UTC-6
    assert to_local(datetime(2026, 10, 3, 16, 0), "America/Edmonton").strftime("%H:%M") == "10:00"
    assert to_local(utc, "America/Toronto").strftime("%H:%M") == "12:00"
    assert to_local(None, "America/Edmonton") is None
    assert zone("not/a-zone").key == "America/Edmonton"                         # bad value falls back safely


@pytest.mark.parametrize("phone,expected", [
    ("+14035550123", "America/Edmonton"), ("(780) 555-0123", "America/Edmonton"),
    ("+16045550123", "America/Vancouver"), ("+14165550123", "America/Toronto"),
    ("+19025550123", "America/Halifax"), ("+12125550123", None), ("", None),
])
def test_timezone_for_phone(phone, expected):
    assert timezone_for_phone(phone) == expected


@pytest.mark.asyncio
async def test_slots_use_contractor_local_business_hours():
    contractor = _make_contractor()
    contractor.timezone = "America/Edmonton"
    contractor.calendar_config = {"business_hours_start": "08:00", "business_hours_end": "18:00"}
    slots = await CalendarService(contractor).get_available_slots("plumbing", "standard", 3)
    assert slots
    for slot in slots:
        start = datetime.fromisoformat(slot["iso_start"])
        assert start.utcoffset() is not None and start.utcoffset().total_seconds() in (-6 * 3600, -7 * 3600)
        assert 8 <= start.hour < 18                                              # local hours, not UTC
        assert to_local(start, "America/Edmonton").hour == start.hour


async def _client(db, contractor):
    async def _dep():
        yield db
    app.dependency_overrides[get_db] = _dep
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                             cookies={SESSION_COOKIE: create_session_token(str(contractor.id))})


@pytest.mark.asyncio
async def test_settings_offers_and_keeps_canadian_timezone(db):
    contractor = _make_contractor(email="tz@example.com")
    contractor.timezone = "America/Edmonton"
    db.add(contractor)
    await db.commit()
    c = await _client(db, contractor)
    try:
        async with c:
            html = (await c.get("/portal/settings")).text
    finally:
        app.dependency_overrides.clear()
    assert '<option value="America/Edmonton" selected>' in html
    assert 'value="America/Vancouver"' in html and 'value="America/Toronto"' in html


@pytest.mark.asyncio
async def test_lead_page_shows_appointment_in_contractor_time(db):
    from app.models.lead import Lead
    contractor = _make_contractor(email="tz2@example.com")
    contractor.timezone = "America/Edmonton"
    db.add(contractor)
    await db.commit()
    lead = Lead(contractor_id=contractor.id, call_id="call_tz1", phone="+14035550142", caller_name="Pat",
                appointment_status="booked", appointment_time=datetime(2026, 10, 3, 16, 0, tzinfo=timezone.utc))
    db.add(lead)
    await db.commit()
    c = await _client(db, contractor)
    try:
        async with c:
            html = (await c.get(f"/portal/leads/{lead.id}")).text
    finally:
        app.dependency_overrides.clear()
    assert "10:00 AM" in html and "04:00 PM" not in html


@pytest.mark.asyncio
async def test_signup_infers_timezone_from_phone(db):
    async def _dep():
        yield db
    app.dependency_overrides[get_db] = _dep
    try:
        with patch("asyncio.create_task", side_effect=lambda c, *a, **k: c.close() or None):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
                await c.post("/onboarding", data={
                    "company_name": "Tz Test", "agent_name": "Sam", "email": "tztest@example.com",
                    "password": "Password123", "confirm_password": "Password123", "phone_number": "(403) 555-0177",
                }, headers={"x-forwarded-for": "9.9.9.88"})
    finally:
        app.dependency_overrides.clear()
    row = (await db.execute(select(Contractor).where(Contractor.email == "tztest@example.com"))).scalar_one()
    assert row.timezone == "America/Edmonton"
