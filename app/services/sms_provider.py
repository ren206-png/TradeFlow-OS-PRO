"""
Outbound SMS transport via Telnyx.
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
    return "telnyx"


def is_configured() -> bool:
    return bool(settings.telnyx_api_key and (settings.telnyx_messaging_profile_id or settings.telnyx_from_number))


def _request(to: str, body: str) -> tuple[str, dict]:
    """Return (url, kwargs) for httpx.post."""
    payload: dict = {"to": to, "text": body}
    if settings.telnyx_from_number:
        payload["from"] = settings.telnyx_from_number
    if settings.telnyx_messaging_profile_id:
        payload["messaging_profile_id"] = settings.telnyx_messaging_profile_id
    return _TELNYX_URL, {
        "json": payload,
        "headers": {"Authorization": f"Bearer {settings.telnyx_api_key}"},
    }


def _message_id(resp: httpx.Response) -> str:
    return (resp.json().get("data") or {}).get("id", "")


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
