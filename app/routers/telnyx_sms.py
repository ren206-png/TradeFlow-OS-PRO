"""
Inbound Telnyx SMS webhook. Configure on the Telnyx Messaging Profile:
  https://tradesflowos.com/telnyx/sms
Replies are sent as new outbound messages (Telnyx has no TwiML-style response).
"""
from __future__ import annotations

import base64
import json
import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.routers.twilio_sms import handle_inbound_sms
from app.services.sms_compliance import STOP_KEYWORDS
from app.services.sms_provider import send_sms

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/telnyx", tags=["telnyx"])

_MAX_SKEW_SECONDS = 300
TELNYX_HANDLED_KEYWORDS = STOP_KEYWORDS | {"start", "unstop", "help"}


def verify_telnyx_signature(raw_body: bytes, signature_b64: str, timestamp: str) -> bool:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    if not (settings.telnyx_public_key and signature_b64 and timestamp):
        return False
    try:
        if abs(time.time() - int(timestamp)) > _MAX_SKEW_SECONDS:
            return False
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(settings.telnyx_public_key))
        key.verify(base64.b64decode(signature_b64), timestamp.encode() + b"|" + raw_body)
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


@router.post("/sms")
async def inbound_sms(request: Request, db: AsyncSession = Depends(get_db)):
    raw = await request.body()
    if not verify_telnyx_signature(
        raw,
        request.headers.get("telnyx-signature-ed25519", ""),
        request.headers.get("telnyx-timestamp", ""),
    ):
        logger.warning("telnyx: rejected webhook with invalid or missing signature from %s", request.client)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid Telnyx signature.")

    try:
        data = json.loads(raw)["data"]
    except (ValueError, KeyError, TypeError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Malformed payload.")

    if data.get("event_type") != "message.received":
        return {"ok": True}

    payload = data.get("payload") or {}
    phone = ((payload.get("from") or {}).get("phone_number") or "").strip()
    to_list = payload.get("to") or [{}]
    to_number = ((to_list[0] if to_list else {}).get("phone_number") or "").strip()
    text = payload.get("text") or ""
    if not phone:
        return {"ok": True}

    reply = await handle_inbound_sms(phone, text, to_number, db)
    # The Telnyx messaging profile auto-replies to opt-out/opt-in/help keywords (and blocks
    # sends after STOP), so only record those here; replying too would double-message.
    if reply and text.strip().lower() not in TELNYX_HANDLED_KEYWORDS:
        await send_sms(phone, reply, "inbound_reply")
    return {"ok": True}
