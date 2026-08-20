"""
Lead-Source Webhook Ingest — Phase 2.

POST /ingest/lead/{tenant_api_key}  — accept arbitrary lead payloads
GET  /ingest/docs/{tenant_api_key}  — returns JSON documentation of payload format

Signed with X-TradeFlow-Signature: HMAC-SHA256(body, contractor.webhook_secret) header.
Feature flag: lead_source_ingest (default OFF).
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Optional, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.contractor import Contractor

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ingest", tags=["lead_ingest"])


# ---------------------------------------------------------------------------
# Schema — extra="allow" so unknown fields are preserved in metadata
# ---------------------------------------------------------------------------

class InboundLeadPayload(BaseModel):
    model_config = ConfigDict(extra="allow")  # accept unknown fields; stored in metadata

    name: Optional[str] = None
    phone: str                              # required
    issue: Optional[str] = None
    source: str = "webhook"
    form_submission_id: Optional[str] = None  # idempotency key

    @field_validator("phone")
    @classmethod
    def phone_strip(cls, v: str) -> str:
        return v.strip()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _verify_ingest_signature(
    raw_body: bytes, webhook_secret: str, header_value: str
) -> bool:
    """Verify X-TradeFlow-Signature: HMAC-SHA256(body, webhook_secret)."""
    if not webhook_secret:
        return False
    expected = hmac.new(
        webhook_secret.encode(),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(header_value, expected)


async def _get_contractor_by_api_key(
    tenant_api_key: str, db: AsyncSession
) -> Contractor:
    result = await db.execute(
        select(Contractor).where(
            Contractor.api_key == tenant_api_key,
            Contractor.is_active.is_(True),
        )
    )
    contractor = result.scalar_one_or_none()
    if contractor is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Contractor not found.",
        )
    return contractor


# ---------------------------------------------------------------------------
# POST /ingest/lead/{tenant_api_key}
# ---------------------------------------------------------------------------

@router.post("/lead/{tenant_api_key}", status_code=200)
async def ingest_lead(
    tenant_api_key: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_tradeflow_signature: Optional[str] = Header(default=None),
):
    """
    Ingest an arbitrary lead payload from an external webhook source.
    Signs: X-TradeFlow-Signature: HMAC-SHA256(raw body bytes, webhook_secret)
    """
    raw_body = await request.body()

    # Parse JSON manually so we can forward extra fields
    import json
    try:
        raw_data = json.loads(raw_body)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body.")

    # Validate with Pydantic (extra fields allowed)
    try:
        payload = InboundLeadPayload(**raw_data)
    except Exception as exc:
        logger.warning("lead_ingest: validation error | err=%s", exc)
        raise HTTPException(status_code=422, detail=str(exc))

    # Step 1: Look up contractor
    contractor = await _get_contractor_by_api_key(tenant_api_key, db)
    tenant_id = str(contractor.id)

    # Step 2: Feature flag — if OFF, accept and return 200
    from app.services.feature_flags import is_enabled
    flag_on = await is_enabled(tenant_id, "lead_source_ingest", db)
    if not flag_on:
        logger.debug("lead_ingest: flag off for tenant=%s", tenant_id)
        return {"status": "accepted"}

    # Step 3: Verify signature
    webhook_secret = getattr(contractor, "webhook_secret", None) or ""
    if not webhook_secret:
        logger.warning("lead_ingest: no webhook_secret for tenant=%s", tenant_id)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Signature verification required.")

    sig_header = x_tradeflow_signature or ""
    if not _verify_ingest_signature(raw_body, webhook_secret, sig_header):
        logger.info("lead_ingest: invalid signature | tenant=%s", tenant_id)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid signature.")

    # Step 4: Idempotency
    if payload.form_submission_id:
        from app.models.callback_request import CallbackRequest
        existing = await db.execute(
            select(CallbackRequest).where(
                CallbackRequest.form_submission_id == payload.form_submission_id
            )
        )
        if existing.scalar_one_or_none():
            logger.info(
                "lead_ingest: idempotency hit | form_id=%s", payload.form_submission_id
            )
            return {"status": "duplicate_ignored"}

    # Steps 5+: Delegate to shared callback pipeline
    try:
        from app.services.callback_pipeline import _trigger_callback_pipeline
        result = await _trigger_callback_pipeline(
            contractor=contractor,
            name=payload.name,
            phone=payload.phone,
            issue=payload.issue,
            source=payload.source,
            form_submission_id=payload.form_submission_id,
            db=db,
        )
        await db.commit()
        return result
    except Exception as exc:
        logger.error("lead_ingest: pipeline error | tenant=%s err=%s", tenant_id, exc)
        await db.rollback()
        return {"status": "error", "detail": "Internal error — request logged."}


# ---------------------------------------------------------------------------
# GET /ingest/docs/{tenant_api_key}
# ---------------------------------------------------------------------------

@router.get("/docs/{tenant_api_key}", status_code=200)
async def ingest_docs(
    tenant_api_key: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Returns JSON documentation of expected payload format and signing instructions.
    """
    contractor = await _get_contractor_by_api_key(tenant_api_key, db)

    return {
        "endpoint": f"POST /ingest/lead/{tenant_api_key}",
        "description": "Ingest an inbound lead from an external source and trigger an instant AI callback.",
        "authentication": {
            "method": "HMAC-SHA256 signature",
            "header": "X-TradeFlow-Signature",
            "key": "Your webhook_secret (provided in the contractor dashboard)",
            "algorithm": "HMAC-SHA256(raw_request_body_bytes, webhook_secret)",
            "note": "Compute the HMAC over the raw JSON body bytes, hex-encode, send in X-TradeFlow-Signature header.",
        },
        "payload": {
            "phone": "string (required) — E.164 format, e.g. +15551234567",
            "name": "string (optional) — lead's full name",
            "issue": "string (optional) — description of the issue or service needed",
            "source": 'string (optional, default "webhook") — originating source identifier',
            "form_submission_id": "string (optional) — unique ID for idempotency; duplicate submissions are ignored",
            "additional_fields": "Any extra fields are accepted and stored as metadata.",
        },
        "feature_flag": "lead_source_ingest must be enabled for your tenant.",
        "example_python": (
            "import hashlib, hmac, json, requests\n"
            "body = json.dumps({'phone': '+15551234567', 'name': 'Jane', 'source': 'crm'}).encode()\n"
            "sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()\n"
            f"requests.post('https://tradesflowos.com/ingest/lead/{tenant_api_key}',\n"
            "    data=body, headers={'X-TradeFlow-Signature': sig, 'Content-Type': 'application/json'})"
        ),
        "responses": {
            "200 status=calling": "Call initiated immediately.",
            "200 status=scheduled": "Outside quiet hours (8am-9pm local). SMS acknowledgment sent; call scheduled.",
            "200 status=duplicate_ignored": "form_submission_id already processed.",
            "200 status=rejected": "Phone failed validation (invalid format, rate limit, blocked prefix).",
            "403": "Invalid signature.",
            "404": "Contractor not found.",
        },
    }
