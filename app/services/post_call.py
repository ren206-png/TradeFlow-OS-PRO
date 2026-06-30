from __future__ import annotations

"""
Post-call analysis: Claude scores the transcript, then optionally
sends a review-request or follow-up SMS.
"""

import json
import logging
import re
from typing import Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.lead import Lead
from app.services.sms import SMSService

logger = logging.getLogger(__name__)

_ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
_MODEL = "claude-3-5-haiku-20241022"

_SYSTEM_PROMPT = """\
You are an expert call analyst for a home-services contractor business.
Analyze the provided call transcript and return a JSON object with exactly
these fields:
  - summary (string): 1-2 sentence summary of what the call was about
  - sentiment (string): one of "positive", "neutral", or "negative"
  - follow_up_recommended (boolean): should the contractor follow up with this lead?
  - review_recommended (boolean): did the call go well enough to ask for a review?
  - notes (string): any important notes for the contractor

Return ONLY valid JSON, optionally wrapped in a ```json ... ``` code block.
"""


class PostCallAnalyser:
    """Analyse a completed call transcript with Claude and update the lead record."""

    async def analyse(
        self,
        call_session,
        transcript: str,
        contractor,
        db: AsyncSession,
    ) -> dict:
        """
        Score the call, update the lead in DB, and optionally send an SMS.

        Returns a dict: {"success": bool, "sentiment": str, "summary": str, "sms_sent": bool}
        """
        # ------------------------------------------------------------------
        # 1. Call Claude via httpx
        # ------------------------------------------------------------------
        try:
            analysis = await self._call_claude(transcript)
        except Exception as exc:
            logger.exception("PostCallAnalyser: Claude API call failed: %s", exc)
            return {"success": False, "error": str(exc)}

        summary: str = analysis.get("summary", "")
        sentiment: str = analysis.get("sentiment", "neutral")
        follow_up_recommended: bool = bool(analysis.get("follow_up_recommended", False))
        review_recommended: bool = bool(analysis.get("review_recommended", False))

        # ------------------------------------------------------------------
        # 2. Update lead record
        # ------------------------------------------------------------------
        sms_sent = False
        try:
            lead: Optional[Lead] = None
            if call_session.lead_id:
                result = await db.execute(select(Lead).where(Lead.id == call_session.lead_id))
                lead = result.scalar_one_or_none()

            if lead:
                lead.ai_summary = summary
                lead.sentiment = sentiment
                if follow_up_recommended:
                    lead.follow_up_recommended = True
                await db.commit()

                # ----------------------------------------------------------
                # 3a. Optionally send review-request SMS
                # ----------------------------------------------------------
                if (
                    review_recommended
                    and getattr(contractor, "review_link", None)
                    and lead.phone
                ):
                    try:
                        sms = SMSService(contractor)
                        sms.send_review_request(
                            phone=lead.phone,
                            name=lead.caller_name or "there",
                            review_link=contractor.review_link,
                        )
                        lead.review_requested = True
                        await db.commit()
                        sms_sent = True
                        logger.info(
                            "Review request SMS sent | lead_id=%s phone=%s",
                            lead.id, lead.phone,
                        )
                    except Exception as sms_exc:
                        logger.warning("PostCallAnalyser: SMS failed: %s", sms_exc)

                # ----------------------------------------------------------
                # 3b. Mark follow_up if lead is not booked
                # ----------------------------------------------------------
                if follow_up_recommended and lead.appointment_status != "booked":
                    lead.follow_up_recommended = True
                    await db.commit()

        except Exception as db_exc:
            logger.exception("PostCallAnalyser: DB update failed: %s", db_exc)
            return {"success": False, "error": str(db_exc)}

        return {
            "success": True,
            "sentiment": sentiment,
            "summary": summary,
            "sms_sent": sms_sent,
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _call_claude(self, transcript: str) -> dict:
        """POST to Anthropic messages API and return parsed JSON analysis."""
        headers = {
            "x-api-key": settings.anthropic_api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        body = {
            "model": _MODEL,
            "max_tokens": 512,
            "system": _SYSTEM_PROMPT,
            "messages": [
                {
                    "role": "user",
                    "content": f"Transcript:\n\n{transcript}",
                }
            ],
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(_ANTHROPIC_URL, headers=headers, json=body)
            response.raise_for_status()

        data = response.json()
        raw_text: str = data["content"][0]["text"]
        return self._parse_json(raw_text)

    @staticmethod
    def _parse_json(text: str) -> dict:
        """Extract and parse JSON from Claude's response (with or without code fence)."""
        # Try to find ```json ... ``` block first
        match = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
        if match:
            return json.loads(match.group(1))
        # Fall back: try to parse the whole text as JSON
        return json.loads(text)
