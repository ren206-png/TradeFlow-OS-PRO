"""Signup hardening: email verification, number search plan, provisioning claim/cap/failure, greeting."""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.config import settings
from app.models.call import CallSession
from app.models.contractor import Contractor
from app.services import provisioning, signup
from app.services.claude_agent import ClaudeAgent


async def _account(db, email="owner@example.ca", phone="(587) 555-0100"):
    return await signup.create_account(
        db, business_name="Test Plumbing", email=email, password="SecurePass1!", phone=phone,
        trades=["Plumbing"], service_areas=["Edmonton"],
    )


def _factory(db):
    class _Ctx:
        async def __aenter__(self_inner):
            return db
        async def __aexit__(self_inner, *a):
            return False
    return lambda: _Ctx()


def test_disposable_and_invalid_emails_rejected():
    assert signup.email_problem("x@mailinator.com")
    assert signup.email_problem("x@formtests.info")
    assert signup.email_problem("no-at-sign")
    assert signup.email_problem("real@acmeplumbing.ca") is None
    assert signup.normalize_email("  Bob@Example.CA ") == "bob@example.ca"


def test_verify_token_roundtrip_and_tamper():
    token = signup.make_verify_token("abc", "a@b.ca")
    assert signup.read_verify_token(token) == {"id": "abc", "email": "a@b.ca"}
    assert signup.read_verify_token(token + "x") is None


def test_number_search_plan_canadian_then_us():
    country, codes = provisioning.number_search_plan("+15875550100")
    assert country == "CA" and codes[0] == "587" and len(codes) == len(set(codes)) <= 10
    country, codes = provisioning.number_search_plan("+12125550100")
    assert country == "US" and codes[0] == "212"


@pytest.mark.asyncio
async def test_new_account_awaits_verification_with_placeholder_number(db):
    c = await _account(db)
    assert c.provisioning_status == "awaiting_verification"
    assert c.phone_number.startswith("pending:") and c.email_verified_at is None
    assert c.timezone == "America/Edmonton"


@pytest.mark.asyncio
async def test_unverified_account_is_never_provisioned(db):
    c = await _account(db)
    with patch("app.database.async_session_factory", _factory(db)), \
         patch("app.services.provisioning.RetellClient") as rc:
        res = await provisioning.provision_contractor_by_id(str(c.id))
    assert res["success"] is False
    rc.assert_not_called()


@pytest.mark.asyncio
async def test_verified_account_gets_agent_with_webhook_and_canadian_number(db, monkeypatch):
    monkeypatch.setattr(settings, "retell_api_key", "k")
    c = await _account(db)
    token = signup.make_verify_token(str(c.id), c.email)
    with patch("app.services.provisioning.asyncio.create_task", lambda coro: coro.close()), \
         patch("app.services.signup.asyncio.create_task", lambda coro: coro.close()):
        assert await signup.confirm_email(db, token) is not None
    client = AsyncMock()
    client.create_agent.return_value = {"agent_id": "agent_1"}
    client.purchase_phone_number.return_value = {"phone_number": "+15875559999"}
    with patch("app.database.async_session_factory", _factory(db)), \
         patch("app.services.provisioning.RetellClient", return_value=client), \
         patch("app.services.provisioning.asyncio.create_task", lambda coro: coro.close()):
        res = await provisioning.provision_contractor_by_id(str(c.id))
    assert res["success"] is True
    cfg = client.create_agent.call_args.args[0]
    assert cfg["webhook_url"] == provisioning.CALL_EVENTS_WEBHOOK_URL
    assert client.purchase_phone_number.call_args.kwargs["country_code"] == "CA"
    refreshed = (await db.execute(select(Contractor).where(Contractor.id == c.id))).scalar_one()
    assert refreshed.provisioning_status == "active" and refreshed.phone_number == "+15875559999"


@pytest.mark.asyncio
async def test_daily_cap_queues_and_alerts_once(db, monkeypatch):
    monkeypatch.setattr(settings, "retell_api_key", "k")
    monkeypatch.setattr(settings, "provisioning_daily_cap", 0)
    c = await _account(db)
    from datetime import datetime, timezone
    c.email_verified_at = datetime.now(tz=timezone.utc)
    await db.commit()
    alert = AsyncMock()
    with patch("app.database.async_session_factory", _factory(db)), \
         patch("app.services.provisioning.RetellClient") as rc, \
         patch("app.services.notifications.notify_admin", alert):
        await provisioning.provision_contractor_by_id(str(c.id))
        await provisioning.provision_contractor_by_id(str(c.id))
    rc.assert_not_called()
    assert alert.await_count == 1
    assert (await db.execute(select(Contractor.provisioning_status).where(Contractor.id == c.id))).scalar_one() == "queued"


@pytest.mark.asyncio
async def test_no_number_available_marks_failed_and_alerts(db, monkeypatch):
    monkeypatch.setattr(settings, "retell_api_key", "k")
    c = await _account(db)
    from datetime import datetime, timezone
    c.email_verified_at = datetime.now(tz=timezone.utc)
    await db.commit()
    client = AsyncMock()
    client.create_agent.return_value = {"agent_id": "agent_2"}
    client.purchase_phone_number.side_effect = RuntimeError("no stock")
    alert = AsyncMock()
    with patch("app.database.async_session_factory", _factory(db)), \
         patch("app.services.provisioning.RetellClient", return_value=client), \
         patch("app.services.notifications.notify_admin", alert):
        res = await provisioning.provision_contractor_by_id(str(c.id))
    assert res["success"] is False
    row = (await db.execute(select(Contractor).where(Contractor.id == c.id))).scalar_one()
    assert row.provisioning_status == "failed" and row.provisioning_attempts == 1 and row.retell_agent_id == "agent_2"
    alert.assert_awaited_once()


@pytest.mark.asyncio
async def test_opening_greeting_discloses_ai_and_recording(db, contractor):
    session = CallSession(id=uuid.uuid4(), retell_call_id="c1", contractor_id=contractor.id, status="active",
                          conversation_history=[])
    agent = ClaudeAgent(contractor=contractor, call_session=session, db=db)
    text = await agent.opening_greeting("ABC Plumbing Ltd", "Alex")
    assert "AI" in text and "may be recorded" in text and "ABC Plumbing Ltd" in text
    assert session.conversation_history[-1]["role"] == "assistant"
