from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import PLAN_LIMITS, settings
from app.database import get_db
from app.models.contractor import Contractor
from app.services.billing import BillingService
from app.utils.auth import get_contractor_from_api_key

router = APIRouter(prefix="/billing", tags=["billing"])
logger = logging.getLogger(__name__)

_STRIPE_BASE = "https://api.stripe.com/v1"


# ---------------------------------------------------------------------------
# POST /billing/create-checkout
# ---------------------------------------------------------------------------

@router.post("/create-checkout")
async def create_checkout(
    request: Request,
    contractor: Contractor = Depends(get_contractor_from_api_key),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Create a Stripe Checkout Session for the chosen plan. Returns {checkout_url}."""
    body = await request.json()
    plan: str = body.get("plan", "starter")

    if plan not in PLAN_LIMITS:
        raise HTTPException(status_code=400, detail=f"Unknown plan: {plan!r}")

    if not settings.stripe_secret_key:
        return {"checkout_url": "https://stripe.com/checkout/disabled-in-dev"}

    price_id = PLAN_LIMITS[plan]["price_id"]

    # Ensure the contractor has a Stripe customer
    billing = BillingService()
    if not contractor.stripe_customer_id:
        await billing.create_customer(contractor)
        await db.flush()

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{_STRIPE_BASE}/checkout/sessions",
            headers={"Authorization": f"Bearer {settings.stripe_secret_key}"},
            data={
                "customer": contractor.stripe_customer_id,
                "mode": "subscription",
                "line_items[0][price]": price_id,
                "line_items[0][quantity]": "1",
                "success_url": "https://app.tradeflow.pro/billing/success",
                "cancel_url": "https://app.tradeflow.pro/billing/cancel",
                "metadata[contractor_id]": str(contractor.id),
                "metadata[plan]": plan,
            },
        )
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.error("Stripe checkout error: %s", exc.response.text)
            raise HTTPException(status_code=502, detail="Stripe error creating checkout session.")

        session = resp.json()

    return {"checkout_url": session.get("url", "")}


# ---------------------------------------------------------------------------
# POST /billing/webhook
# ---------------------------------------------------------------------------

@router.post("/webhook")
async def stripe_webhook(
    request: Request,
    stripe_signature: Optional[str] = Header(None, alias="stripe-signature"),
) -> dict:
    """Verify Stripe webhook signature and route to BillingService.handle_webhook."""
    raw_body = await request.body()

    if settings.stripe_webhook_secret:
        if not stripe_signature:
            raise HTTPException(status_code=400, detail="Missing stripe-signature header.")
        _verify_stripe_signature(raw_body, stripe_signature)

    import json
    try:
        event = json.loads(raw_body)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload.")

    await BillingService().handle_webhook(event)
    return {"status": "ok"}


def _verify_stripe_signature(payload: bytes, header: str) -> None:
    """
    Stripe webhook signature format:
        t=<timestamp>,v1=<hmac_sha256_hex>,...
    We verify the v1 signature using HMAC-SHA256(t=<timestamp>.<payload>, webhook_secret).
    """
    parts: dict = {}
    for item in header.split(","):
        if "=" in item:
            k, v = item.split("=", 1)
            parts[k.strip()] = v.strip()

    timestamp = parts.get("t", "")
    v1_sig = parts.get("v1", "")

    if not timestamp or not v1_sig:
        raise HTTPException(status_code=400, detail="Malformed stripe-signature header.")

    signed_payload = f"{timestamp}.".encode() + payload
    expected = hmac.new(
        settings.stripe_webhook_secret.encode(),
        signed_payload,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected, v1_sig):
        raise HTTPException(status_code=403, detail="Invalid Stripe webhook signature.")


# ---------------------------------------------------------------------------
# GET /billing/status
# ---------------------------------------------------------------------------

@router.get("/status")
async def billing_status(
    contractor: Contractor = Depends(get_contractor_from_api_key),
) -> dict:
    """Return current billing status for the authenticated contractor."""
    plan = contractor.plan or "starter"
    limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["starter"])

    trial_ends_at = None
    if contractor.trial_ends_at is not None:
        trial_ends_at = contractor.trial_ends_at.isoformat()

    return {
        "plan": plan,
        "subscription_status": contractor.subscription_status or "trial",
        "calls_this_month": contractor.calls_this_month or 0,
        "calls_limit": limits["calls"],
        "sms_this_month": contractor.sms_this_month or 0,
        "sms_limit": limits["sms"],
        "trial_ends_at": trial_ends_at,
    }
