"""Send a test lead notification email via Resend SMTP."""
import smtplib
import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

smtp_host = os.environ.get("SMTP_HOST", "")
smtp_port = int(os.environ.get("SMTP_PORT", "587"))
smtp_user = os.environ.get("SMTP_USER", "")
smtp_password = os.environ.get("SMTP_PASSWORD", "")
smtp_from = os.environ.get("SMTP_FROM", "noreply@tradesflowos.com")

TO = "ren206@gmail.com"
smtp_from = "onboarding@resend.dev"  # Resend's shared domain — works without verification

subject = "Test — New Lead: John Smith (Plumbing)"

text = """
You have a new lead!

Name:     John Smith
Phone:    +1 (587) 555-0100
Trade:    Plumbing
Priority: High
Status:   Pending
Address:  123 Main St, Calgary
Issue:    Burst pipe under kitchen sink, water actively leaking.

View in portal: https://tradesflowos.com/portal/leads

-- TradeFlow OS Test Notification
"""

html = """
<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background:#f3f4f6;font-family:Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6;padding:32px 0;">
    <tr><td align="center">
      <table width="560" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.08);">
        <tr>
          <td style="background:#1e40af;padding:24px 32px;">
            <p style="margin:0;color:#ffffff;font-size:20px;font-weight:700;">TradeFlow</p>
            <p style="margin:4px 0 0;color:#bfdbfe;font-size:13px;">New Lead Notification — TEST</p>
          </td>
        </tr>
        <tr>
          <td style="padding:24px 32px 0;">
            <span style="display:inline-block;background:#f97316;color:#fff;font-size:12px;font-weight:700;padding:4px 12px;border-radius:999px;letter-spacing:0.05em;text-transform:uppercase;">High Priority</span>
          </td>
        </tr>
        <tr>
          <td style="padding:16px 32px 24px;">
            <h2 style="margin:0 0 16px;font-size:22px;color:#111827;">John Smith</h2>
            <table width="100%" cellpadding="0" cellspacing="0">
              <tr>
                <td style="padding:8px 0;border-bottom:1px solid #f3f4f6;color:#6b7280;font-size:13px;width:110px;">Phone</td>
                <td style="padding:8px 0;border-bottom:1px solid #f3f4f6;color:#111827;font-size:14px;font-weight:600;">+1 (587) 555-0100</td>
              </tr>
              <tr>
                <td style="padding:8px 0;border-bottom:1px solid #f3f4f6;color:#6b7280;font-size:13px;">Trade</td>
                <td style="padding:8px 0;border-bottom:1px solid #f3f4f6;color:#111827;font-size:14px;">Plumbing</td>
              </tr>
              <tr>
                <td style="padding:8px 0;border-bottom:1px solid #f3f4f6;color:#6b7280;font-size:13px;">Status</td>
                <td style="padding:8px 0;border-bottom:1px solid #f3f4f6;color:#111827;font-size:14px;">Pending</td>
              </tr>
              <tr>
                <td style="padding:8px 0;border-bottom:1px solid #f3f4f6;color:#6b7280;font-size:13px;">Address</td>
                <td style="padding:8px 0;border-bottom:1px solid #f3f4f6;color:#111827;font-size:14px;">123 Main St, Calgary</td>
              </tr>
              <tr>
                <td style="padding:8px 0;color:#6b7280;font-size:13px;vertical-align:top;">Issue</td>
                <td style="padding:8px 0;color:#111827;font-size:14px;">Burst pipe under kitchen sink, water actively leaking.</td>
              </tr>
            </table>
          </td>
        </tr>
        <tr>
          <td style="padding:0 32px 32px;">
            <a href="https://tradesflowos.com/portal/leads"
               style="display:inline-block;background:#1e40af;color:#ffffff;text-decoration:none;padding:12px 28px;border-radius:8px;font-size:14px;font-weight:600;">
              View Lead in Portal →
            </a>
          </td>
        </tr>
        <tr>
          <td style="background:#f9fafb;padding:16px 32px;border-top:1px solid #e5e7eb;">
            <p style="margin:0;color:#9ca3af;font-size:12px;">TradeFlow OS · tradesflowos.com · This is a test notification.</p>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>
"""

print(f"Connecting to {smtp_host}:{smtp_port} as {smtp_user}...")

msg = MIMEMultipart("alternative")
msg["Subject"] = subject
msg["From"] = smtp_from
msg["To"] = TO
msg.attach(MIMEText(text, "plain"))
msg.attach(MIMEText(html, "html"))

with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
    server.ehlo()
    server.starttls()
    server.login(smtp_user, smtp_password)
    server.sendmail(smtp_from, [TO], msg.as_string())

print(f"✅ Test email sent to {TO}")
