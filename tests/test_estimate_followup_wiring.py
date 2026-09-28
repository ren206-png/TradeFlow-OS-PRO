"""Portal estimate logging + hourly drip job wiring."""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy import select

from app.database import get_db
from app.main import app
from app.models.estimate import Estimate
from app.models.feature_flag import FeatureFlag
from app.models.lead import Lead
from app.utils.sessions import SESSION_COOKIE, create_session_token
from tests.test_auth import _make_contractor


async def _seed(db, flag_on: bool = True):
    contractor = _make_contractor(email=f"{uuid.uuid4().hex[:8]}@example.com")
    contractor.phone_number = f"+1555{uuid.uuid4().int % 10**7:07d}"
    db.add(contractor)
    lead = Lead(
        id=uuid.uuid4(),
        contractor_id=contractor.id,
        call_id=f"call-{uuid.uuid4().hex[:8]}",
        phone="+15875550100",
        caller_name="Jamie",
    )
    db.add(lead)
    if flag_on:
        db.add(FeatureFlag(tenant_id=str(contractor.id), flag_key="estimate_followup", enabled=True))
    await db.commit()
    return contractor, lead


def _client(db, contractor):
    async def _dep():
        yield db
    app.dependency_overrides[get_db] = _dep
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        cookies={SESSION_COOKIE: create_session_token(str(contractor.id))},
    )


@pytest.mark.asyncio
async def test_log_estimate_creates_and_enrolls(db):
    contractor, lead = await _seed(db)
    try:
        async with _client(db, contractor) as client:
            resp = await client.post(f"/portal/leads/{lead.id}/estimate", data={"amount": "$1,250.50"})
        assert resp.status_code == 302
    finally:
        app.dependency_overrides.clear()

    est = (await db.execute(select(Estimate).where(Estimate.lead_id == lead.id))).scalar_one()
    assert est.estimate_value_cents == 125050
    assert est.status == "sent"
    assert est.caller_phone == lead.phone
    assert est.followup_enrolled_at is not None
    assert est.followup_step == 0


@pytest.mark.asyncio
async def test_log_estimate_not_enrolled_when_flag_off(db):
    contractor, lead = await _seed(db, flag_on=False)
    try:
        async with _client(db, contractor) as client:
            await client.post(f"/portal/leads/{lead.id}/estimate", data={"amount": ""})
    finally:
        app.dependency_overrides.clear()

    est = (await db.execute(select(Estimate).where(Estimate.lead_id == lead.id))).scalar_one()
    assert est.estimate_value_cents is None
    assert est.followup_enrolled_at is None


@pytest.mark.asyncio
async def test_mark_accepted_stops_drip(db):
    contractor, lead = await _seed(db)
    est = Estimate(tenant_id=contractor.id, lead_id=lead.id, caller_phone=lead.phone, status="sent")
    db.add(est)
    await db.commit()
    try:
        async with _client(db, contractor) as client:
            resp = await client.post(f"/portal/estimates/{est.id}/status", data={"status": "accepted"})
        assert resp.status_code == 302
    finally:
        app.dependency_overrides.clear()
    await db.refresh(est)
    assert est.status == "accepted"


@pytest.mark.asyncio
async def test_other_tenant_cannot_change_estimate(db):
    owner, lead = await _seed(db)
    intruder, _ = await _seed(db)
    est = Estimate(tenant_id=owner.id, lead_id=lead.id, caller_phone=lead.phone, status="sent")
    db.add(est)
    await db.commit()
    try:
        async with _client(db, intruder) as client:
            await client.post(f"/portal/estimates/{est.id}/status", data={"status": "declined"})
    finally:
        app.dependency_overrides.clear()
    await db.refresh(est)
    assert est.status == "sent"


@pytest.mark.asyncio
async def test_hourly_job_runs_only_due_steps(db):
    from app.services import scheduler

    contractor, lead = await _seed(db)
    now = datetime.now(tz=timezone.utc)
    due = Estimate(tenant_id=contractor.id, lead_id=lead.id, caller_phone=lead.phone, status="sent",
                   followup_enrolled_at=now - timedelta(days=3), followup_step=0)
    not_due = Estimate(tenant_id=contractor.id, lead_id=lead.id, caller_phone=lead.phone, status="sent",
                       followup_enrolled_at=now - timedelta(days=1), followup_step=0)
    done = Estimate(tenant_id=contractor.id, lead_id=lead.id, caller_phone=lead.phone, status="accepted",
                    followup_enrolled_at=now - timedelta(days=30), followup_step=0)
    db.add_all([due, not_due, done])
    await db.commit()

    @asynccontextmanager
    async def _factory():
        yield db

    run_step = AsyncMock()
    with patch("app.database.async_session_factory", _factory), \
         patch("app.services.estimate_followup.EstimateFollowupService.run_step", run_step):
        await scheduler._estimate_followup_job()

    called_ids = [c.args[0] for c in run_step.await_args_list]
    assert called_ids == [due.id]


@pytest.mark.asyncio
async def test_lead_detail_renders_estimate_card(db):
    contractor, lead = await _seed(db)
    try:
        async with _client(db, contractor) as client:
            empty = await client.get(f"/portal/leads/{lead.id}")
            await client.post(f"/portal/leads/{lead.id}/estimate", data={"amount": "900"})
            filled = await client.get(f"/portal/leads/{lead.id}")
    finally:
        app.dependency_overrides.clear()
    assert empty.status_code == 200 and "I sent an estimate" in empty.text
    assert filled.status_code == 200 and "$900.00" in filled.text and "0 of 3 follow-ups sent" in filled.text
