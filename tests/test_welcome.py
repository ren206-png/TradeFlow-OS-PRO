"""Welcome and subscription thank-you emails."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services import welcome
from app.services.welcome import build_subscription_thanks_email, build_welcome_email, should_thank


def test_welcome_email_content_and_escaping():
    subject, html_body, text = build_welcome_email("<b>Summit</b> Plumbing")
    assert "Welcome to TradeFlow" in subject
    assert "&lt;b&gt;Summit&lt;/b&gt;" in html_body and "<b>Summit</b>" not in html_body
    assert "https://tradesflowos.com/portal/leads" in html_body and "portal/leads" in text


def test_thanks_email_names_plan():
    subject, html_body, text = build_subscription_thanks_email("CoolAir", "pro")
    assert subject == "Thank you for subscribing to TradeFlow Pro"
    assert "Pro" in html_body and "Pro plan" in text


@pytest.mark.parametrize("prev_status,prev_plan,new_status,new_plan,expected", [
    ("trial", "starter", "active", "pro", True),       # first paid activation
    ("active", "starter", "active", "pro", True),      # upgrade
    ("active", "pro", "active", "pro", False),         # renewal / no change
    ("active", "pro", "active", None, False),          # unknown price id
    ("trial", "starter", "past_due", "pro", False),    # not active
    ("past_due", "pro", "active", "pro", True),        # reactivated
])
def test_should_thank(prev_status, prev_plan, new_status, new_plan, expected):
    assert should_thank(prev_status, prev_plan, new_status, new_plan) is expected


@pytest.mark.asyncio
async def test_send_welcome_uses_smtp_helper():
    with patch.object(welcome, "_send_email", return_value=True) as send:
        assert await welcome.send_welcome_email("owner@example.com", "Summit") is True
    to, subject, _, _ = send.call_args.args
    assert to == "owner@example.com" and "Welcome" in subject


@pytest.mark.asyncio
async def test_no_email_address_skips():
    with patch.object(welcome, "_send_email") as send:
        assert await welcome.send_welcome_email("", "Summit") is False
    send.assert_not_called()


@pytest.mark.asyncio
async def test_stripe_activation_sends_thanks_once(db):
    from app.services.billing import BillingService
    from tests.test_auth import _make_contractor

    c = _make_contractor(email="owner@example.com")
    c.stripe_customer_id = "cus_123"
    c.subscription_status = "trial"
    db.add(c)
    await db.commit()

    from app.config import settings
    event = {"type": "customer.subscription.created", "data": {"object": {
        "customer": "cus_123", "status": "active",
        "items": {"data": [{"price": {"id": settings.stripe_pro_price_id or "price_pro_x"}}]}}}}

    handler = BillingService().handle_webhook
    with patch("app.services.welcome._send_email", return_value=True) as send:
        await handler(event, db)
        event["type"] = "customer.subscription.updated"
        await handler(event, db)  # renewal-style update: no second email
    assert send.call_count == 1
    assert send.call_args.args[0] == "owner@example.com"


@pytest.mark.parametrize("addr,ok", [
    ("owner@example.com", True),
    ("rencoenterprise25@gmail.com", True),
    ("demo-e22ef66a@tradesflowos.internal", False),
    ("a@b.test", False),
    ("", False),
    ("not-an-email", False),
    ("a@@b.com", False),
])
def test_is_deliverable_address(addr, ok):
    from app.services.notifications import is_deliverable_address
    assert is_deliverable_address(addr) is ok


def test_placeholder_address_never_hits_smtp():
    from app.services import notifications
    with patch.object(notifications, "_smtp_enabled", return_value=True), \
         patch("smtplib.SMTP") as smtp:
        assert notifications._send_email("demo-e22ef66a@tradesflowos.internal", "s", "<p>h</p>", "t") is False
    smtp.assert_not_called()
