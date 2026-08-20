"""
Webform Instant Callback — Phase 2.

POST /webform/callback/{tenant_api_key}  — public signed endpoint
GET  /webform/snippet/{tenant_api_key}   — embeddable JS snippet

Auth model: timestamp-based HMAC
  signature = HMAC-SHA256(tenant_api_key + timestamp, webhook_secret)
  Valid within 5 minutes of now.
  The GET /snippet endpoint renders JS that POSTs to callback with a fresh signature.

Feature flag: webform_callback (default OFF).
  OFF → accept request, return 200, do nothing.
  ON  → full pipeline.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.contractor import Contractor
from app.services.callback_pipeline import _trigger_callback_pipeline

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webform", tags=["webform"])

_SIG_WINDOW_SECONDS = 300  # 5 minutes


# ---------------------------------------------------------------------------
# Request schema
# ---------------------------------------------------------------------------

class WebformCallbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = None
    phone: str
    issue: Optional[str] = None
    source: Optional[str] = "webform"
    form_submission_id: Optional[str] = None
    timestamp: int          # Unix epoch seconds — provided by the JS snippet
    signature: str          # HMAC-SHA256(tenant_api_key + str(timestamp), webhook_secret)

    @field_validator("phone")
    @classmethod
    def phone_must_be_e164(cls, v: str) -> str:
        v = v.strip()
        if not v.startswith("+") or not v[1:].isdigit():
            raise ValueError("phone must be in E.164 format (+15551234567)")
        return v


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _verify_webform_signature(
    tenant_api_key: str,
    timestamp: int,
    signature: str,
    webhook_secret: str,
) -> bool:
    """Verify timestamp freshness and HMAC signature."""
    now = int(time.time())
    if abs(now - timestamp) > _SIG_WINDOW_SECONDS:
        return False
    signing_material = f"{tenant_api_key}{timestamp}".encode()
    expected = hmac.new(
        webhook_secret.encode(),
        signing_material,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(signature, expected)


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
# POST /webform/callback/{tenant_api_key}
# ---------------------------------------------------------------------------

@router.post("/callback/{tenant_api_key}", status_code=200)
async def webform_callback(
    tenant_api_key: str,
    payload: WebformCallbackRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Public signed endpoint — accepts webform lead and triggers instant callback.
    """
    # Step 1: Look up contractor
    contractor = await _get_contractor_by_api_key(tenant_api_key, db)
    tenant_id = str(contractor.id)

    # Step 2: Feature flag — if OFF, silently accept and return 200
    from app.services.feature_flags import is_enabled
    flag_on = await is_enabled(tenant_id, "webform_callback", db)
    if not flag_on:
        logger.debug("webform_callback: flag off for tenant=%s", tenant_id)
        return {"status": "accepted"}

    # Step 3: Verify HMAC signature
    webhook_secret = getattr(contractor, "webhook_secret", None) or ""
    if not webhook_secret:
        logger.warning("webform_callback: no webhook_secret for tenant=%s", tenant_id)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Signature verification failed.")

    if not _verify_webform_signature(
        tenant_api_key=tenant_api_key,
        timestamp=payload.timestamp,
        signature=payload.signature,
        webhook_secret=webhook_secret,
    ):
        logger.info("webform_callback: invalid signature | tenant=%s", tenant_id)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid signature.")

    # Step 4: Idempotency — if form_submission_id already processed, return 200
    if payload.form_submission_id:
        from app.models.callback_request import CallbackRequest
        existing = await db.execute(
            select(CallbackRequest).where(
                CallbackRequest.form_submission_id == payload.form_submission_id
            )
        )
        if existing.scalar_one_or_none():
            logger.info(
                "webform_callback: idempotency hit | form_id=%s", payload.form_submission_id
            )
            return {"status": "duplicate_ignored"}

    # Steps 5-10: Delegate to shared pipeline
    try:
        result = await _trigger_callback_pipeline(
            contractor=contractor,
            name=payload.name,
            phone=payload.phone,
            issue=payload.issue,
            source=payload.source or "webform",
            form_submission_id=payload.form_submission_id,
            db=db,
        )
        await db.commit()
        return result
    except Exception as exc:
        logger.error("webform_callback: pipeline error | tenant=%s err=%s", tenant_id, exc)
        await db.rollback()
        return {"status": "error", "detail": "Internal error — request logged."}


# ---------------------------------------------------------------------------
# GET /webform/snippet/{tenant_api_key}
# Returns embeddable JS + form HTML
# ---------------------------------------------------------------------------

@router.get("/snippet/{tenant_api_key}", response_class=HTMLResponse)
async def webform_snippet(
    tenant_api_key: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Returns an embeddable HTML/JS snippet for the contractor's website.
    The snippet posts to /webform/callback/{tenant_api_key} with a signed timestamp.
    The signature is computed server-side when the snippet is served (no secrets in JS).
    """
    contractor = await _get_contractor_by_api_key(tenant_api_key, db)
    webhook_secret = getattr(contractor, "webhook_secret", None) or ""

    # Generate a signed timestamp valid for _SIG_WINDOW_SECONDS
    ts = int(time.time())
    sig = ""
    if webhook_secret:
        signing_material = f"{tenant_api_key}{ts}".encode()
        sig = hmac.new(
            webhook_secret.encode(),
            signing_material,
            hashlib.sha256,
        ).hexdigest()

    agent_name = getattr(contractor, "agent_name", None) or contractor.name
    callback_url = f"/webform/callback/{tenant_api_key}"

    html = f"""<!-- TradeFlow Callback Widget -->
<div id="tf-callback-form" style="font-family:sans-serif;max-width:400px;padding:20px;border:1px solid #ddd;border-radius:8px;">
  <h3 style="margin-top:0">Request a Callback from {agent_name}</h3>
  <div id="tf-form-content">
    <label for="tf-name">Your Name</label><br>
    <input id="tf-name" type="text" placeholder="Jane Smith" style="width:100%;margin-bottom:10px;padding:8px;box-sizing:border-box;"><br>
    <label for="tf-phone">Phone Number (E.164, e.g. +15551234567)</label><br>
    <input id="tf-phone" type="tel" placeholder="+15551234567" style="width:100%;margin-bottom:10px;padding:8px;box-sizing:border-box;"><br>
    <label for="tf-issue">How can we help?</label><br>
    <textarea id="tf-issue" rows="3" style="width:100%;margin-bottom:10px;padding:8px;box-sizing:border-box;"></textarea><br>
    <button id="tf-submit" onclick="tfSubmitCallback()" style="background:#2563eb;color:#fff;border:none;padding:10px 20px;border-radius:4px;cursor:pointer;width:100%">
      Request Callback
    </button>
  </div>
  <div id="tf-result" style="display:none;padding:12px;background:#f0fdf4;border-radius:4px;"></div>
</div>
<script>
async function tfSubmitCallback() {{
  const name = document.getElementById('tf-name').value.trim();
  const phone = document.getElementById('tf-phone').value.trim();
  const issue = document.getElementById('tf-issue').value.trim();
  const btn = document.getElementById('tf-submit');
  btn.disabled = true;
  btn.textContent = 'Sending...';
  try {{
    const resp = await fetch('{callback_url}', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{
        name: name || null,
        phone: phone,
        issue: issue || null,
        source: 'webform_snippet',
        timestamp: {ts},
        signature: '{sig}'
      }})
    }});
    const data = await resp.json();
    document.getElementById('tf-form-content').style.display = 'none';
    const result = document.getElementById('tf-result');
    result.style.display = 'block';
    if (data.status === 'calling') {{
      result.textContent = "We're calling you right now!";
    }} else if (data.status === 'scheduled') {{
      result.textContent = "Got it! We'll call you when our office opens at 8am.";
    }} else {{
      result.textContent = 'Request received! We will be in touch shortly.';
    }}
  }} catch(e) {{
    btn.disabled = false;
    btn.textContent = 'Request Callback';
    alert('Something went wrong. Please try again.');
  }}
}}
</script>"""

    return HTMLResponse(content=html)
