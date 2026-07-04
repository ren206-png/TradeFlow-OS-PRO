"""
Inbound Twilio SMS webhook — handles STOP / UNSTOP / HELP keywords.
Configure this URL in your Twilio Messaging Service:
  https://tradesflowos.com/twilio/sms
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Form, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.sms_compliance import handle_inbound_keyword

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/twilio", tags=["twilio"])


@router.post("/sms")
async def inbound_sms(
    request: Request,
    From: str = Form(...),
    Body: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    """
    Handles inbound SMS from Twilio.
    - STOP / STOPALL / UNSUBSCRIBE → opt-out, reply confirmation
    - START / UNSTOP / YES         → opt back in, reply confirmation
    - HELP / INFO                  → send help message
    - Anything else                → log and ignore (no reply)
    """
    phone = From.strip()
    body = Body.strip()
    logger.info("Inbound SMS | from=%s body=%r", phone, body[:80])

    reply = await handle_inbound_keyword(phone, body, db)

    if reply:
        # Return TwiML response
        twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Message>{reply}</Message>
</Response>"""
        return Response(content=twiml, media_type="application/xml")

    # No keyword matched — return empty TwiML (no reply)
    return Response(
        content='<?xml version="1.0" encoding="UTF-8"?><Response></Response>',
        media_type="application/xml",
    )
