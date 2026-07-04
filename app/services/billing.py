from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import PLAN_LIMITS, settings

logger = logging.getLogger(__name__)

_STRIPE_BASE = "https://api.stripe.com/v1"


class BillingService:
    """Stripe billing integration using raw httpx calls."""

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {settings.stripe_secret_key}"}

    def _is_configured(self) -> bool:
        return bool(settings.stripe_secret_key)

    # ------------------------------------------------------------------
    # Customer
    # ------------------------------------------------------------------

    async def create_customer(self, contractor) -> str:
        """Creates a Stripe customer, saves stripe_customer_id on contractor. Returns customer_id."""
        if not self._is_configured():
            logger.debug("Stripe not configured — skipping create_customer")
            return ""

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{_STRIPE_BASE}/customers",
                headers=self._headers(),
                data={"name": contractor.name, "metadata[contractor_id]": str(contractor.id)},
            )
            resp.raise_for_status()
            customer_id: str = resp.json()["id"]

        contractor.stripe_customer_id = customer_id
        logger.info("Stripe customer created | contractor=%s customer=%s", contractor.name, customer_id)
        return customer_id

    # ------------------------------------------------------------------
    # Subscription
    # ------------------------------------------------------------------

    async def create_subscription(self, contractor, plan: str) -> str:
        """Creates a Stripe subscription for the plan. Returns subscription_id."""
        if not self._is_configured():
            logger.debug("Stripe not configured — skipping create_subscription")
            return ""

        price_id = PLAN_LIMITS.get(plan, {}).get("price_id", "")
        if not price_id:
            raise ValueError(f"Unknown plan: {plan!r}")

        customer_id = contractor.stripe_customer_id
        if not customer_id:
            customer_id = await self.create_customer(contractor)

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{_STRIPE_BASE}/subscriptions",
                headers=self._headers(),
                data={
                    "customer": customer_id,
                    "items[0][price]": price_id,
                    "metadata[contractor_id]": str(contractor.id),
                },
            )
            resp.raise_for_status()
            body = resp.json()
            subscription_id: str = body["id"]

        contractor.stripe_subscription_id = subscription_id
        contractor.subscription_status = "active"
        contractor.plan = plan
        logger.info(
            "Stripe subscription created | contractor=%s plan=%s sub=%s",
            contractor.name, plan, subscription_id,
        )

        # Update Mailchimp plan tag (fire-and-forget)
        if contractor.email:
            import asyncio as _asyncio
            from app.services.mailchimp import update_plan_tag as _mc_plan
            _asyncio.create_task(_mc_plan(email=contractor.email, new_plan=plan))

        return subscription_id

    async def cancel_subscription(self, contractor) -> None:
        """Cancels the Stripe subscription at period end."""
        if not self._is_configured():
            logger.debug("Stripe not configured — skipping cancel_subscription")
            return

        sub_id = contractor.stripe_subscription_id
        if not sub_id:
            return

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{_STRIPE_BASE}/subscriptions/{sub_id}",
                headers=self._headers(),
                data={"cancel_at_period_end": "true"},
            )
            resp.raise_for_status()

        contractor.subscription_status = "canceled"
        logger.info("Stripe subscription canceled | contractor=%s sub=%s", contractor.name, sub_id)

    # ------------------------------------------------------------------
    # Usage
    # ------------------------------------------------------------------

    def _reset_if_new_period(self, contractor) -> bool:
        """
        Resets monthly counters if billing period has rolled over.
        Uses billing_period_start (reliable anchor) not updated_at (fragile).
        Returns True if a reset occurred.
        """
        now = datetime.now(tz=timezone.utc)
        period_start = getattr(contractor, "billing_period_start", None)

        if period_start is not None:
            if period_start.tzinfo is None:
                period_start = period_start.replace(tzinfo=timezone.utc)
            if period_start.month == now.month and period_start.year == now.year:
                return False  # still in same billing period

        # New period — reset all counters and anchor the period
        contractor.calls_this_month = 0
        contractor.sms_this_month = 0
        contractor.minutes_this_month = 0
        contractor.billing_period_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        logger.info("Billing period reset | contractor=%s period=%s", contractor.name, contractor.billing_period_start)
        return True

    async def check_usage_limit(self, contractor, resource: str) -> dict:
        """Returns {"allowed": bool, "used": int, "limit": int, "plan": str}"""
        plan = getattr(contractor, "plan", "starter") or "starter"
        limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["starter"])

        if resource == "calls":
            used = getattr(contractor, "calls_this_month", 0) or 0
            limit = limits["calls"]
        elif resource == "sms":
            used = getattr(contractor, "sms_this_month", 0) or 0
            limit = limits["sms"]
        elif resource == "minutes":
            used = getattr(contractor, "minutes_this_month", 0) or 0
            limit = limits.get("max_call_mins", 10) * limits["calls"]  # total minute budget
        else:
            return {"allowed": True, "used": 0, "limit": 9999, "plan": plan}

        allowed = used < limit
        if not allowed:
            logger.warning(
                "Usage cap hit | contractor=%s resource=%s used=%d limit=%d plan=%s",
                contractor.name, resource, used, limit, plan,
            )
        return {
            "allowed": allowed,
            "used": used,
            "limit": limit,
            "plan": plan,
        }

    async def increment_usage(self, contractor, resource: str, db: AsyncSession, minutes: int = 0) -> None:
        """Increments usage counters. Resets on new billing period using billing_period_start."""
        self._reset_if_new_period(contractor)

        if resource == "calls":
            contractor.calls_this_month = (getattr(contractor, "calls_this_month", 0) or 0) + 1
            if minutes > 0:
                contractor.minutes_this_month = (getattr(contractor, "minutes_this_month", 0) or 0) + minutes
        elif resource == "sms":
            contractor.sms_this_month = (getattr(contractor, "sms_this_month", 0) or 0) + 1

        await db.flush()
        logger.info(
            "Usage incremented | contractor=%s resource=%s calls=%d sms=%d minutes=%d",
            contractor.name, resource,
            contractor.calls_this_month,
            contractor.sms_this_month,
            getattr(contractor, "minutes_this_month", 0),
        )

    # ------------------------------------------------------------------
    # Webhook
    # ------------------------------------------------------------------

    async def handle_webhook(self, event: dict, db: AsyncSession) -> None:
        """Handles Stripe webhook events."""
        from sqlalchemy import select
        from app.models.contractor import Contractor

        event_type: str = event.get("type", "")
        data_obj: dict = event.get("data", {}).get("object", {})

        logger.info("Stripe webhook | type=%s", event_type)

        if event_type == "invoice.paid":
            sub_id = data_obj.get("subscription")
            if sub_id:
                logger.info("Invoice paid | subscription=%s", sub_id)
                # subscription_status will be set to active via customer.subscription.updated
                # but we log here for audit purposes

        elif event_type == "invoice.payment_failed":
            customer_id = data_obj.get("customer")
            result = await db.execute(select(Contractor).where(Contractor.stripe_customer_id == customer_id))
            contractor = result.scalar_one_or_none()
            if contractor:
                contractor.subscription_status = "past_due"
                await db.commit()

        elif event_type == "customer.subscription.deleted":
            customer_id = data_obj.get("customer")
            result = await db.execute(select(Contractor).where(Contractor.stripe_customer_id == customer_id))
            contractor = result.scalar_one_or_none()
            if contractor:
                contractor.subscription_status = "cancelled"
                contractor.plan = "starter"
                await db.commit()

        elif event_type in ("customer.subscription.created", "customer.subscription.updated"):
            event_data = data_obj
            customer_id = event_data.get("customer")
            status = event_data.get("status", "active")
            # Resolve plan from price ID
            price_id = ""
            try:
                price_id = event_data["items"]["data"][0]["price"]["id"]
            except (KeyError, IndexError):
                pass
            plan = None
            if price_id and price_id == settings.stripe_starter_price_id:
                plan = "starter"
            elif price_id and price_id == settings.stripe_pro_price_id:
                plan = "pro"

            result = await db.execute(select(Contractor).where(Contractor.stripe_customer_id == customer_id))
            contractor = result.scalar_one_or_none()
            if contractor:
                contractor.subscription_status = status
                if plan:
                    contractor.plan = plan
                await db.commit()
                logger.info("Stripe webhook: updated contractor %s plan=%s status=%s", contractor.name, plan, status)
