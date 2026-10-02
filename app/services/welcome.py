"""
Contractor lifecycle emails: welcome on signup, thank-you when a paid plan starts.
Sends via the SMTP helper in notifications.py; failures are logged, never raised.
"""
from __future__ import annotations

import asyncio
import html
import logging

from app.services.notifications import _send_email

logger = logging.getLogger(__name__)

PORTAL_URL = "https://tradesflowos.com/portal/leads"
SETTINGS_URL = "https://tradesflowos.com/portal/settings"

_PLAN_LABELS = {"starter": "Starter", "pro": "Pro", "enterprise": "Enterprise"}


def _wrap(title: str, body_html: str) -> str:
    return f"""<div style="font-family:-apple-system,Segoe UI,Arial,sans-serif;max-width:560px;margin:0 auto;color:#1f2937;line-height:1.55">
  <h1 style="font-size:22px;margin:0 0 16px">{title}</h1>
  {body_html}
  <p style="margin-top:28px;color:#6b7280;font-size:13px">Questions? Just reply to this email and a real person will get back to you.<br>— The TradeFlow team</p>
</div>"""


def _button(url: str, label: str) -> str:
    return (f'<p style="margin:24px 0"><a href="{url}" style="background:#2563eb;color:#fff;'
            f'text-decoration:none;padding:12px 20px;border-radius:8px;font-weight:600">{label}</a></p>')


def build_welcome_email(business_name: str, verify_url: str = "") -> tuple[str, str, str]:
    """Confirm-your-email message (the Mailchimp drip does the welcoming; the number-ready email does the onboarding)."""
    name = html.escape(business_name or "there")
    subject = "Confirm your email to activate your TradeFlow AI number"
    button = _button(verify_url, "Confirm my email") if verify_url else ""
    body = f"""<p>Hi {name},</p>
<p>Please confirm your email so we can set up your dedicated AI phone number. The link is valid for 7 days.</p>
{button}
<p>Your number appears in your portal within a few minutes of confirming.</p>"""
    text = (f"Hi {business_name or 'there'},\n\n"
            "Please confirm your email so we can set up your dedicated AI phone number (link valid for 7 days):\n"
            + (f"{verify_url}\n\n" if verify_url else "\n")
            + "Your number appears in your portal within a few minutes of confirming.\n\n"
            "Questions? Just reply to this email.\n— The TradeFlow team")
    return subject, _wrap("Confirm your email", body), text


def build_subscription_thanks_email(business_name: str, plan: str) -> tuple[str, str, str]:
    name = html.escape(business_name or "there")
    label = _PLAN_LABELS.get((plan or "").lower(), (plan or "your").capitalize())
    subject = f"Thank you for subscribing to TradeFlow {label}"
    body = f"""<p>Hi {name},</p>
<p>Thank you for subscribing to the <strong>{label}</strong> plan. Your subscription is active and your AI receptionist will keep answering every call, day and night.</p>
<p>You can see your plan, usage, and billing details any time in Settings.</p>
{_button(SETTINGS_URL, "View my plan")}
<p>We really appreciate your trust in TradeFlow. If there's anything that would make it more useful for your business, we'd love to hear it.</p>"""
    text = (f"Hi {business_name or 'there'},\n\n"
            f"Thank you for subscribing to the {label} plan. Your subscription is active and your AI "
            "receptionist will keep answering every call, day and night.\n\n"
            f"View your plan and billing: {SETTINGS_URL}\n\n"
            "Questions? Just reply to this email.\n— The TradeFlow team")
    return subject, _wrap("Thank you! 🎉", body), text


async def send_welcome_email(email: str, business_name: str, verify_url: str = "") -> bool:
    if not email:
        return False
    subject, html_body, text = build_welcome_email(business_name, verify_url)
    ok = await asyncio.to_thread(_send_email, email, subject, html_body, text)
    logger.info("welcome email %s | to=%s", "sent" if ok else "not sent", email)
    return ok


def build_number_ready_email(business_name: str, phone_number: str) -> tuple[str, str, str]:
    from app.utils.phone import format_display
    name = html.escape(business_name or "there")
    shown = html.escape(format_display(phone_number))
    subject = "Your TradeFlow AI number is ready"
    body = f"""<p>Hi {name},</p>
<p>Your AI receptionist is live. Your dedicated number is:</p>
<p style="font-size:24px;font-weight:700;margin:12px 0">{shown}</p>
<p><strong>Three steps to your first booked job:</strong></p>
<ol>
  <li><strong>Call your AI number yourself</strong> to hear exactly what your customers will hear.</li>
  <li><strong>Forward your business line to it</strong> after hours (or all the time). The Setup page in your portal has the steps for your carrier.</li>
  <li><strong>Add your booking link and Google review link</strong> in Settings so the assistant can send them to customers.</li>
</ol>
{_button(PORTAL_URL, "Open my portal")}"""
    text = (f"Hi {business_name or 'there'},\n\nYour AI receptionist is live. Your number: {format_display(phone_number)}\n"
            f"Call it yourself, then forward your business line to it.\nPortal: {PORTAL_URL}\n— The TradeFlow team")
    return subject, _wrap("Your AI number is ready", body), text


async def send_number_ready_email(email: str, business_name: str, phone_number: str) -> bool:
    if not email:
        return False
    subject, html_body, text = build_number_ready_email(business_name, phone_number)
    ok = await asyncio.to_thread(_send_email, email, subject, html_body, text)
    logger.info("number-ready email %s | to=%s", "sent" if ok else "not sent", email)
    return ok


async def send_subscription_thanks_email(email: str, business_name: str, plan: str) -> bool:
    if not email:
        return False
    subject, html_body, text = build_subscription_thanks_email(business_name, plan)
    ok = await asyncio.to_thread(_send_email, email, subject, html_body, text)
    logger.info("subscription thank-you email %s | to=%s plan=%s", "sent" if ok else "not sent", email, plan)
    return ok


def should_thank(prev_status: str | None, prev_plan: str | None, new_status: str, new_plan: str | None) -> bool:
    """Thank on first activation, or on a plan change while active. Not on routine renewals."""
    if new_status != "active":
        return False
    if prev_status != "active":
        return True
    return bool(new_plan and new_plan != prev_plan)
