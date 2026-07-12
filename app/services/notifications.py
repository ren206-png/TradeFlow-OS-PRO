from __future__ import annotations

import logging
import smtplib
import asyncio
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from app.config import settings

logger = logging.getLogger(__name__)


def _smtp_enabled() -> bool:
    return bool(settings.smtp_host and settings.smtp_user and settings.smtp_password)


def _send_email(to: str, subject: str, html: str, text: str) -> bool:
    """Send an email via SMTP. Returns True on success."""
    if not _smtp_enabled():
        logger.debug("SMTP not configured — skipping email to %s", to)
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = settings.smtp_from
        msg["To"] = to
        msg.attach(MIMEText(text, "plain"))
        msg.attach(MIMEText(html, "html"))
        # Port 465 = SSL, Port 587 = STARTTLS
        if settings.smtp_port == 465:
            with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=10) as server:
                server.login(settings.smtp_user, settings.smtp_password)
                server.sendmail(settings.smtp_from, [to], msg.as_string())
        else:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as server:
                server.ehlo()
                server.starttls()
                server.login(settings.smtp_user, settings.smtp_password)
                server.sendmail(settings.smtp_from, [to], msg.as_string())
        logger.info("Email sent | to=%s subject=%s", to, subject)
        return True
    except Exception as exc:
        logger.error("Email send failed | to=%s error=%s", to, exc)
        return False


async def notify_new_lead(contractor, lead) -> None:
    """
    Fire-and-forget: notify a contractor about a new lead via SMS + email.
    Called right after a lead record is created/updated.
    """
    # ── Build message content ────────────────────────────────────────
    name = lead.caller_name or "Unknown Caller"
    phone = lead.phone or "—"
    trade = (lead.trade or "General").title()
    problem = lead.problem_summary or "No details captured"
    status = (lead.appointment_status or "new").replace("_", " ").title()
    priority = (lead.priority_level or "normal").title()
    address = lead.service_address or ""
    if lead.city:
        address = f"{address}, {lead.city}".strip(", ")

    portal_url = "https://tradesflowos.com/portal/leads"

    # ── SMS to contractor ────────────────────────────────────────────
    if contractor.phone_number:
        try:
            from app.services.sms import SMSService
            sms_body = (
                f"New {trade} Lead — TradeFlow\n"
                f"Name: {name}\n"
                f"Phone: {phone}\n"
                f"Priority: {priority}\n"
                f"Issue: {problem[:100]}\n"
                f"View: {portal_url}"
            )
            sms = SMSService(contractor)
            await sms._send_async(contractor.phone_number, sms_body, "new_lead")
            logger.info("Lead notification SMS sent | contractor=%s lead=%s", contractor.name, lead.id)
        except Exception as exc:
            logger.error("Lead notification SMS failed | contractor=%s error=%s", contractor.name, exc)

    # ── Email to contractor ──────────────────────────────────────────
    if contractor.email:
        subject = f"New {trade} Lead: {name} — TradeFlow"

        text = (
            f"You have a new lead!\n\n"
            f"Name:     {name}\n"
            f"Phone:    {phone}\n"
            f"Trade:    {trade}\n"
            f"Priority: {priority}\n"
            f"Status:   {status}\n"
            f"Address:  {address or '—'}\n"
            f"Issue:    {problem}\n\n"
            f"View in portal: {portal_url}\n"
        )

        priority_color = {
            "Emergency": "#ef4444",
            "High": "#f97316",
            "Medium": "#eab308",
            "Normal": "#6366f1",
        }.get(priority, "#6366f1")

        html = f"""
<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background:#f3f4f6;font-family:Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6;padding:32px 0;">
    <tr><td align="center">
      <table width="560" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.08);">

        <!-- Header -->
        <tr>
          <td style="background:#1e40af;padding:24px 32px;">
            <p style="margin:0;color:#ffffff;font-size:20px;font-weight:700;">TradeFlow</p>
            <p style="margin:4px 0 0;color:#bfdbfe;font-size:13px;">New Lead Notification</p>
          </td>
        </tr>

        <!-- Priority badge -->
        <tr>
          <td style="padding:24px 32px 0;">
            <span style="display:inline-block;background:{priority_color};color:#fff;font-size:12px;font-weight:700;padding:4px 12px;border-radius:999px;letter-spacing:0.05em;text-transform:uppercase;">{priority} Priority</span>
          </td>
        </tr>

        <!-- Lead info -->
        <tr>
          <td style="padding:16px 32px 24px;">
            <h2 style="margin:0 0 16px;font-size:22px;color:#111827;">{name}</h2>
            <table width="100%" cellpadding="0" cellspacing="0">
              <tr>
                <td style="padding:8px 0;border-bottom:1px solid #f3f4f6;color:#6b7280;font-size:13px;width:110px;">Phone</td>
                <td style="padding:8px 0;border-bottom:1px solid #f3f4f6;color:#111827;font-size:14px;font-weight:600;">{phone}</td>
              </tr>
              <tr>
                <td style="padding:8px 0;border-bottom:1px solid #f3f4f6;color:#6b7280;font-size:13px;">Trade</td>
                <td style="padding:8px 0;border-bottom:1px solid #f3f4f6;color:#111827;font-size:14px;">{trade}</td>
              </tr>
              <tr>
                <td style="padding:8px 0;border-bottom:1px solid #f3f4f6;color:#6b7280;font-size:13px;">Status</td>
                <td style="padding:8px 0;border-bottom:1px solid #f3f4f6;color:#111827;font-size:14px;">{status}</td>
              </tr>
              {"<tr><td style='padding:8px 0;border-bottom:1px solid #f3f4f6;color:#6b7280;font-size:13px;'>Address</td><td style='padding:8px 0;border-bottom:1px solid #f3f4f6;color:#111827;font-size:14px;'>" + address + "</td></tr>" if address else ""}
              <tr>
                <td style="padding:8px 0;color:#6b7280;font-size:13px;vertical-align:top;">Issue</td>
                <td style="padding:8px 0;color:#111827;font-size:14px;">{problem}</td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- CTA -->
        <tr>
          <td style="padding:0 32px 32px;">
            <a href="{portal_url}"
               style="display:inline-block;background:#1e40af;color:#ffffff;text-decoration:none;padding:12px 28px;border-radius:8px;font-size:14px;font-weight:600;">
              View Lead in Portal →
            </a>
          </td>
        </tr>

        <!-- Footer -->
        <tr>
          <td style="background:#f9fafb;padding:16px 32px;border-top:1px solid #e5e7eb;">
            <p style="margin:0;color:#9ca3af;font-size:12px;">TradeFlow OS · tradesflowos.com · You're receiving this because you have an active contractor account.</p>
          </td>
        </tr>

      </table>
    </td></tr>
  </table>
</body>
</html>
"""
        await asyncio.get_running_loop().run_in_executor(
            None, _send_email, contractor.email, subject, html, text
        )


async def notify_appointment_booked(contractor, lead) -> None:
    """Notify contractor when an appointment is booked."""
    name = lead.caller_name or "Unknown Caller"
    phone = lead.phone or "—"
    trade = (lead.trade or "General").title()
    apt_time = lead.appointment_time.strftime("%A, %b %d at %I:%M %p") if lead.appointment_time else "Time TBD"
    address = lead.service_address or ""
    if lead.city:
        address = f"{address}, {lead.city}".strip(", ")

    portal_url = "https://tradesflowos.com/portal/leads"

    # SMS
    if contractor.phone_number:
        try:
            from app.services.sms import SMSService
            sms_body = (
                f"Appointment Booked — TradeFlow\n"
                f"{name} | {phone}\n"
                f"{trade} — {apt_time}\n"
                f"{address}\n"
                f"View: {portal_url}"
            )
            sms = SMSService(contractor)
            await sms._send_async(contractor.phone_number, sms_body, "appointment_booked")
        except Exception as exc:
            logger.error("Booking notification SMS failed | contractor=%s error=%s", contractor.name, exc)

    # Email
    if contractor.email:
        subject = f"Appointment Booked: {name} — {apt_time}"
        text = (
            f"An appointment has been booked!\n\n"
            f"Name:  {name}\n"
            f"Phone: {phone}\n"
            f"Trade: {trade}\n"
            f"Time:  {apt_time}\n"
            f"Address: {address or '—'}\n\n"
            f"View in portal: {portal_url}\n"
        )
        html = f"""
<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background:#f3f4f6;font-family:Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6;padding:32px 0;">
    <tr><td align="center">
      <table width="560" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.08);">
        <tr>
          <td style="background:#059669;padding:24px 32px;">
            <p style="margin:0;color:#ffffff;font-size:20px;font-weight:700;">TradeFlow</p>
            <p style="margin:4px 0 0;color:#a7f3d0;font-size:13px;">Appointment Booked</p>
          </td>
        </tr>
        <tr>
          <td style="padding:24px 32px;">
            <h2 style="margin:0 0 4px;font-size:22px;color:#111827;">{name}</h2>
            <p style="margin:0 0 20px;color:#059669;font-size:15px;font-weight:600;">{apt_time}</p>
            <table width="100%" cellpadding="0" cellspacing="0">
              <tr>
                <td style="padding:8px 0;border-bottom:1px solid #f3f4f6;color:#6b7280;font-size:13px;width:110px;">Phone</td>
                <td style="padding:8px 0;border-bottom:1px solid #f3f4f6;color:#111827;font-size:14px;font-weight:600;">{phone}</td>
              </tr>
              <tr>
                <td style="padding:8px 0;border-bottom:1px solid #f3f4f6;color:#6b7280;font-size:13px;">Trade</td>
                <td style="padding:8px 0;border-bottom:1px solid #f3f4f6;color:#111827;font-size:14px;">{trade}</td>
              </tr>
              {"<tr><td style='padding:8px 0;color:#6b7280;font-size:13px;'>Address</td><td style='padding:8px 0;color:#111827;font-size:14px;'>" + address + "</td></tr>" if address else ""}
            </table>
          </td>
        </tr>
        <tr>
          <td style="padding:0 32px 32px;">
            <a href="{portal_url}" style="display:inline-block;background:#059669;color:#ffffff;text-decoration:none;padding:12px 28px;border-radius:8px;font-size:14px;font-weight:600;">View in Portal →</a>
          </td>
        </tr>
        <tr>
          <td style="background:#f9fafb;padding:16px 32px;border-top:1px solid #e5e7eb;">
            <p style="margin:0;color:#9ca3af;font-size:12px;">TradeFlow OS · tradesflowos.com</p>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>
"""
        await asyncio.get_running_loop().run_in_executor(
            None, _send_email, contractor.email, subject, html, text
        )
