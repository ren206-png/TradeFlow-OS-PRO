"""
Outbound SMS transport. SMS_PROVIDER selects the carrier ("twilio" or "telnyx").
Callers get {"success": bool, "sid": str} or {"success": False, "error": str}.
"""
from __future__ import annotations

import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_TELNYX_URL = "https://api.telnyx.com/v2/messages"
_TIMEOUT = 10


def provider() -> str:
    return (settings.sms_provider or "twilio").strip().lower()


def is_configured() -> bool:
    if provider() == "telnyx":
        return bool(settings.telnyx_api_key and (settings.telnyx_messaging_profile_id or settings.telnyx_from_number))
    return bool(settings.twilio_account_sid and settings.twilio_auth_token)


def _request(to: str, body: str) -> tuple[str, dict]:
    """Return (url, kwargs) for httpx.post for the active provider."""
    if provider() == "telnyx":
        payload: dict = {"to": to, "text": body}
        if settings.telnyx_from_number:
            payload["from"] = settings.telnyx_from_number
        if settings.telnyx_messaging_profile_id:
            payload["messaging_profile_id"] = settings.telnyx_messaging_profile_id
        return _TELNYX_URL, {
            "json": payload,
            "headers": {"Authorization": f"Bearer {settings.telnyx_api_key}"},
        }

    data: dict = {"To": to, "Body": body}
    if settings.twilio_messaging_service_sid:
        data["MessagingServiceSid"] = settings.twilio_messaging_service_sid
    else:
        data["From"] = settings.twilio_from_number
    url = f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_account_sid}/Messages.json"
    return url, {"data": data, "auth": (settings.twilio_account_sid, settings.twilio_auth_token)}


def _message_id(resp: httpx.Response) -> str:
    body = resp.json()
    if provider() == "telnyx":
        return (body.get("data") or {}).get("id", "")
    return body.get("sid", "")


def _not_configured(message_type: str) -> dict:
    logger.warning("SMS provider %s not configured — SMS skipped [%s]", provider(), message_type)
    return {"success": False, "error": f"{provider()} not configured"}


async def send_sms(to: str, body: str, message_type: str = "sms") -> dict:
    if not is_configured():
        return _not_configured(message_type)
    try:
        url, kwargs = _request(to, body)
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(url, **kwargs)
        resp.raise_for_status()
        sid = _message_id(resp)
        logger.info("SMS sent | provider=%s id=%s to=%s type=%s", provider(), sid, to, message_type)
        return {"success": True, "sid": sid}
    except Exception as exc:
        logger.error("SMS send failed | provider=%s type=%s err=%s", provider(), message_type, exc)
        return {"success": False, "error": str(exc)}


def send_sms_sync(to: str, body: str, message_type: str = "sms") -> dict:
    if not is_configured():
        return _not_configured(message_type)
    try:
        url, kwargs = _request(to, body)
        resp = httpx.post(url, timeout=_TIMEOUT, **kwargs)
        resp.raise_for_status()
        sid = _message_id(resp)
        logger.info("SMS sent | provider=%s id=%s to=%s type=%s", provider(), sid, to, message_type)
        return {"success": True, "sid": sid}
    except Exception as exc:
        logger.error("SMS send failed | provider=%s type=%s err=%s", provider(), message_type, exc)
        return {"success": False, "error": str(exc)}
