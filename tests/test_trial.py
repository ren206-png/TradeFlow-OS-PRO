"""14-day free trial: set at signup, banner state, call gate, checkout carries trial, reminders."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.config import TRIAL_DAYS
from app.models.contractor import Contractor
from app.routers.billing import _checkout_trial_fields
from app.services import signup
from app.services.billing import trial_days_left, trial_expired
from app.services.welcome import build_trial_ending_email


def _c(**kw):
    base = dict(subscription_status="trial", trial_ends_at=None)
    base.update(kw)
    return Contractor(**base)


@pytest.mark.asyncio
async def test_signup_starts_14_day_trial(db):
    c = await signup.create_account(db, business_name="T", email="t@acme.ca", password="SecurePass1!",
                                    phone="7805550100", trades=["Plumbing"], service_areas=["Edmonton"])
    assert c.subscription_status == "trial"
    delta = c.trial_ends_at.replace(tzinfo=timezone.utc) - datetime.now(tz=timezone.utc) if c.trial_ends_at.tzinfo is None \
        else c.trial_ends_at - datetime.now(tz=timezone.utc)
    assert TRIAL_DAYS - 1 < delta.days + 1 <= TRIAL_DAYS


def test_trial_state_helpers():
    now = datetime.now(tz=timezone.utc)
    assert trial_expired(_c()) is False  # legacy accounts without a trial date never expire
    assert trial_expired(_c(trial_ends_at=now + timedelta(days=3))) is False
    assert trial_days_left(_c(trial_ends_at=now + timedelta(days=3, hours=1))) == 4
    assert trial_expired(_c(trial_ends_at=now - timedelta(hours=1))) is True
    assert trial_expired(_c(trial_ends_at=now - timedelta(days=5), subscription_status="active")) is False
    assert trial_expired(_c(trial_ends_at=now - timedelta(days=5), subscription_status="past_due")) is False
    assert trial_days_left(_c(trial_ends_at=now + timedelta(days=3), subscription_status="active")) is None


def test_checkout_carries_trial_only_when_far_enough_away():
    now = datetime.now(tz=timezone.utc)
    assert "subscription_data[trial_end]" in _checkout_trial_fields(_c(trial_ends_at=now + timedelta(days=5)))
    assert _checkout_trial_fields(_c(trial_ends_at=now + timedelta(hours=30))) == {}
    assert _checkout_trial_fields(_c()) == {}


def test_trial_ending_email_mentions_price_and_link():
    subject, html_body, text = build_trial_ending_email("Acme", 2)
    assert "in 2 days" in subject and "$5.99" in html_body and "portal/subscribe" in text
    assert "tomorrow" in build_trial_ending_email("Acme", 1)[0]
