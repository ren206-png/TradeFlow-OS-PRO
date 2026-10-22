"""Lifecycle emails: day 1, first call, week one, trial expired, payment failed. Each at most once, none once paid."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.models.call import CallSession
from app.models.contractor import Contractor
from app.models.lead import Lead
from app.services import lifecycle_emails as le

NOW = datetime.now(tz=timezone.utc)


_N = iter(range(1000))


async def _make(db, **kw):
    base = dict(name="Acme Plumbing", email="owner@acme.ca", email_verified_at=NOW, provisioning_status="active",
                provisioned_at=NOW - timedelta(days=2), phone_number=f"+1780555{next(_N):04d}", subscription_status="trial",
                trial_ends_at=NOW + timedelta(days=12), is_active=True, owner_phone="+17805550100",
                trades=["Plumbing"], service_areas=["Edmonton"], agent_name="Alex", api_key=__import__("uuid").uuid4().hex)
    base.update(kw)
    c = Contractor(**base)
    db.add(c)
    await db.commit()
    return c


@pytest.fixture
def outbox():
    sent = []
    def fake(to, subject, html, text):
        sent.append((to, subject))
        return True
    with patch("app.services.lifecycle_emails._send_email", fake), patch("app.services.welcome._send_email", fake):
        yield sent


@pytest.mark.asyncio
async def test_day1_sent_once_when_no_calls(db, outbox):
    c = await _make(db)
    assert (await le.run_lifecycle_emails(db))["day1_no_calls"] == 1
    assert (await le.run_lifecycle_emails(db))["day1_no_calls"] == 0
    assert [s for _, s in outbox] == ["Your AI receptionist hasn't taken a call yet"]
    assert "day1_no_calls" in le.sent_keys(c)


@pytest.mark.asyncio
async def test_day1_skipped_when_calls_exist_or_too_new(db, outbox):
    c = await _make(db)
    db.add(CallSession(retell_call_id="call_1", contractor_id=c.id, status="ended", duration_seconds=60))
    await _make(db, email="new@acme.ca", provisioned_at=NOW - timedelta(hours=3))
    await db.commit()
    assert (await le.run_lifecycle_emails(db))["day1_no_calls"] == 0
    assert outbox == []


@pytest.mark.asyncio
async def test_week_one_has_stats_and_sends_once(db, outbox):
    c = await _make(db, provisioned_at=NOW - timedelta(days=8))
    db.add(CallSession(retell_call_id="call_2", contractor_id=c.id, status="ended", duration_seconds=90))
    db.add(Lead(contractor_id=c.id, call_id="lead_a", caller_name="Sam", appointment_status="booked"))
    await db.commit()
    assert (await le.run_lifecycle_emails(db))["week_one"] == 1
    assert (await le.run_lifecycle_emails(db))["week_one"] == 0
    assert outbox[0][1] == "Your first week: 1 call answered"


@pytest.mark.asyncio
async def test_paid_accounts_get_nothing(db, outbox):
    await _make(db, subscription_status="active", provisioned_at=NOW - timedelta(days=8))
    await _make(db, email="p@acme.ca", subscription_status="trialing", provisioned_at=NOW - timedelta(days=8))
    assert sum((await le.run_lifecycle_emails(db)).values()) == 0
    assert outbox == []


@pytest.mark.asyncio
async def test_trial_expired_once_and_not_after_a_week(db, outbox):
    await _make(db, trial_ends_at=NOW - timedelta(hours=2), provisioned_at=NOW - timedelta(days=15))
    await _make(db, email="old@acme.ca", trial_ends_at=NOW - timedelta(days=9), provisioned_at=NOW - timedelta(days=23))
    assert (await le.run_lifecycle_emails(db))["trial_expired"] == 1
    assert (await le.run_lifecycle_emails(db))["trial_expired"] == 0
    assert [to for to, _ in outbox] == ["owner@acme.ca"]


@pytest.mark.asyncio
async def test_trial_ending_reminder_still_sends_once(db, outbox):
    await _make(db, trial_ends_at=NOW + timedelta(days=2, hours=1), provisioned_at=NOW - timedelta(days=12))
    assert (await le.run_lifecycle_emails(db))["trial_ending"] == 1
    assert (await le.run_lifecycle_emails(db))["trial_ending"] == 0


@pytest.mark.asyncio
async def test_first_call_email_once_and_only_for_real_calls(db, outbox, monkeypatch):
    c = await _make(db)
    lead = Lead(contractor_id=c.id, call_id="lead_b", caller_name="Sam Lee", ai_summary="Burst pipe in the basement.", appointment_status="booked")
    db.add(lead)
    await db.commit()
    db.add(CallSession(retell_call_id="short", contractor_id=c.id, status="ended", duration_seconds=4))
    db.add(CallSession(retell_call_id="real", contractor_id=c.id, status="ended", duration_seconds=75, lead_id=lead.id))
    await db.commit()

    from contextlib import asynccontextmanager
    class _F:
        def __call__(self):
            @asynccontextmanager
            async def cm():
                yield db
            return cm()
    monkeypatch.setattr("app.database.async_session_factory", _F())
    assert await le.maybe_send_first_call_email(str(c.id), "short") is False
    assert await le.maybe_send_first_call_email(str(c.id), "real") is True
    assert await le.maybe_send_first_call_email(str(c.id), "real") is False
    assert outbox == [("owner@acme.ca", "Your AI receptionist just took its first call")]


def test_builders_escape_and_mention_key_facts():
    s, h, t = le.build_first_call_email("<b>X</b>", "<i>Sam</i>", "Leak <script>", True)
    assert "<script>" not in h and "&lt;script&gt;" in h and "booked" in h
    s, h, t = le.build_payment_failed_email("Acme")
    assert "update" in s.lower() or "payment" in s.lower()
    assert "TradeFlow OS" in h  # footer
