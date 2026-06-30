"""
Mailchimp integration — subscribes new contractors to the email drip sequence.

Uses Mailchimp Marketing API v3 via httpx (no extra dependency).

On signup:
  1. Upsert the contact into the audience (PUT /members/{hash})
  2. Apply the "TradeFlow Signup" tag → triggers Email 1 in the automation
  3. Set merge fields (FNAME, TRADE, PHONE) so emails can be personalised

Tags used (must exist in Mailchimp audience):
  - "TradeFlow Signup"     → triggers the 5-email drip automation
  - "Starter Plan"         → for plan-specific emails
  - "Pro Plan"             → set when contractor upgrades
"""
from __future__ import annotations

import hashlib
import logging
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


def _md5(email: str) -> str:
    """Mailchimp subscriber hash = MD5 of lowercase email."""
    return hashlib.md5(email.lower().encode()).hexdigest()


def _base_url() -> str:
    return f"https://{settings.mailchimp_server_prefix}.api.mailchimp.com/3.0"


def _is_configured() -> bool:
    return bool(
        settings.mailchimp_api_key
        and settings.mailchimp_server_prefix
        and settings.mailchimp_list_id
    )


async def subscribe_contractor(
    email: str,
    first_name: str,
    trade: str,
    phone: str = "",
    plan: str = "starter",
) -> dict:
    """
    Upsert a contractor into the Mailchimp audience and apply the signup tag.
    Safe to call multiple times — Mailchimp will update rather than duplicate.

    Returns {"success": True} or {"success": False, "error": "..."}
    """
    if not _is_configured():
        logger.info("Mailchimp not configured — skipping subscriber add for %s", email)
        return {"success": False, "error": "Mailchimp not configured"}

    subscriber_hash = _md5(email)
    list_id = settings.mailchimp_list_id
    auth = ("anystring", settings.mailchimp_api_key)

    async with httpx.AsyncClient(timeout=10) as client:
        # Step 1 — Upsert the member
        member_url = f"{_base_url()}/lists/{list_id}/members/{subscriber_hash}"
        member_payload = {
            "email_address": email,
            "status_if_new": "subscribed",   # only sets status for NEW members
            "merge_fields": {
                "FNAME": first_name.split()[0] if first_name else "",
                "LNAME": " ".join(first_name.split()[1:]) if first_name else "",
                "PHONE": phone,
                "TRADE": trade,
                "PLAN": plan.title(),
            },
        }
        member_resp = await client.put(member_url, json=member_payload, auth=auth)

        if member_resp.status_code not in (200, 201):
            logger.error(
                "Mailchimp upsert failed for %s: %s %s",
                email, member_resp.status_code, member_resp.text,
            )
            return {"success": False, "error": member_resp.text}

        logger.info("Mailchimp: upserted %s", email)

        # Step 2 — Apply tags to trigger the drip automation
        plan_tag = "Pro Plan" if plan == "pro" else "Starter Plan"
        tags_url = f"{_base_url()}/lists/{list_id}/members/{subscriber_hash}/tags"
        tags_payload = {
            "tags": [
                {"name": "TradeFlow Signup", "status": "active"},
                {"name": plan_tag, "status": "active"},
            ]
        }
        tags_resp = await client.post(tags_url, json=tags_payload, auth=auth)

        if tags_resp.status_code != 204:
            logger.warning(
                "Mailchimp tag apply failed for %s: %s %s",
                email, tags_resp.status_code, tags_resp.text,
            )
            # Non-fatal — member was added, tags just didn't apply
            return {"success": True, "warning": "Tags not applied — check Mailchimp audience"}

        logger.info("Mailchimp: tags applied for %s (TradeFlow Signup, %s)", email, plan_tag)
        return {"success": True}


async def update_plan_tag(email: str, new_plan: str) -> dict:
    """
    Called when a contractor upgrades. Swaps the plan tag so Mailchimp
    can send upgrade-specific emails.
    """
    if not _is_configured():
        return {"success": False, "error": "Mailchimp not configured"}

    subscriber_hash = _md5(email)
    list_id = settings.mailchimp_list_id
    auth = ("anystring", settings.mailchimp_api_key)

    old_tag = "Starter Plan" if new_plan == "pro" else "Pro Plan"
    new_tag = "Pro Plan" if new_plan == "pro" else "Starter Plan"

    async with httpx.AsyncClient(timeout=10) as client:
        tags_url = f"{_base_url()}/lists/{list_id}/members/{subscriber_hash}/tags"
        tags_payload = {
            "tags": [
                {"name": old_tag, "status": "inactive"},
                {"name": new_tag, "status": "active"},
                {"name": "Upgraded to Pro", "status": "active"},
            ]
        }
        resp = await client.post(tags_url, json=tags_payload, auth=auth)
        if resp.status_code != 204:
            return {"success": False, "error": resp.text}
        return {"success": True}
