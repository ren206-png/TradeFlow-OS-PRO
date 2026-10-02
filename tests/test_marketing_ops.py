"""TikTok pixel, ad attribution, Mailchimp-after-verification, email volume warning, 500 alerts."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

import app.main as main_mod
from app.main import app
from app.services import notifications, signup
from app.utils import attribution


@pytest.mark.asyncio
async def test_landing_captures_utm_cookie_and_renders_pixel_only_when_configured():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as c:
        r = await c.get("/?utm_source=tiktok&utm_campaign=spring&ttclid=abc123")
        assert r.status_code == 200
        assert attribution.COOKIE in r.headers.get("set-cookie", "")
        assert "analytics.tiktok.com" not in r.text  # no pixel id configured
        with patch.dict(main_mod.templates.env.globals, {"tiktok_pixel_id": "PIXEL123"}):
            r2 = await c.get("/")
        assert "analytics.tiktok.com" in r2.text and "PIXEL123" in r2.text


def test_attribution_roundtrip_ignores_junk():
    class Req:  # minimal stand-in
        cookies = {attribution.COOKIE: '%7B%22utm_source%22%3A%22tiktok%22%2C%22evil%22%3A%22x%22%7D'}
    assert attribution.read(Req()) == {"utm_source": "tiktok"}
    Req.cookies = {attribution.COOKIE: "not json"}
    assert attribution.read(Req()) is None


@pytest.mark.asyncio
async def test_mailchimp_subscribes_only_after_email_confirmed(db):
    c = await signup.create_account(db, business_name="Mc Plumbing", email="mc@acme.ca", password="SecurePass1!",
                                    phone="7805550100", trades=["Plumbing"], service_areas=["Edmonton"],
                                    attribution={"utm_source": "tiktok"})
    assert c.attribution == {"utm_source": "tiktok"}
    sub = AsyncMock()
    with patch("app.services.mailchimp.subscribe_contractor", sub), \
         patch("app.services.signup.asyncio.create_task", lambda coro: coro.close()):
        signup.fire_signup_side_effects(c, "Plumbing", "7805550100")
        sub.assert_not_called()
        await signup.confirm_email(db, signup.make_verify_token(str(c.id), c.email))
        assert sub.call_count == 1


def test_email_volume_warning_fires_once(monkeypatch):
    monkeypatch.setattr(notifications.settings, "smtp_user", "admin@example.com")
    notifications._EMAIL_DAY.update(day="", count=0, warned=False)
    sent = []
    monkeypatch.setattr(notifications, "_send_email_raw", lambda *a, **k: sent.append(a) or True)
    for _ in range(notifications.EMAIL_DAILY_WARN + 5):
        notifications._count_email_sent()
    assert len([s for s in sent if "daily limit" in s[1]]) == 1


@pytest.mark.asyncio
async def test_500_alerts_admin_once_per_error():
    main_mod._ERROR_ALERTS.clear()
    alert = AsyncMock()

    @app.get("/__boom", include_in_schema=False)
    async def boom():
        raise ValueError("kaboom")

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    with patch("app.services.notifications.notify_admin", alert):
        async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as c:
            assert (await c.get("/__boom")).status_code == 500
            assert (await c.get("/__boom")).status_code == 500
        import asyncio
        await asyncio.sleep(0.05)
    assert alert.await_count == 1


def test_resend_used_first_then_smtp_fallback(monkeypatch):
    monkeypatch.setattr(notifications.settings, "resend_api_key", "re_test")
    calls = {}

    class Resp:
        status_code = 200
        text = ""

    def fake_post(url, json, headers, timeout):
        calls["url"], calls["json"] = url, json
        return Resp()

    monkeypatch.setattr("httpx.post", fake_post)
    assert notifications._send_email_raw("a@acme.ca", "Hi", "<p>x</p>", "x") is True
    assert calls["url"] == "https://api.resend.com/emails" and calls["json"]["to"] == ["a@acme.ca"]

    Resp.status_code = 403  # e.g. unverified domain -> falls through to SMTP (not configured here)
    monkeypatch.setattr(notifications, "_smtp_enabled", lambda: False)
    assert notifications._send_email_raw("a@acme.ca", "Hi", "<p>x</p>", "x") is False
