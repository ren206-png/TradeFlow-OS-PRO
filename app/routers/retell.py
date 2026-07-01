from __future__ import annotations
"""
Retell AI integration — two independent surfaces:

1. WebSocket  /llm-websocket/{call_id}
   Retell opens this WebSocket for every call (Custom LLM agent type).
   Protocol reference: https://docs.retellai.com/api-references/llm-websocket

2. HTTP POST  /retell/webhook
   Retell fires lifecycle events (call_started, call_ended, call_analyzed, transfer_*).
   Signature header: x-retell-signature  format: v={timestamp_ms},d={hmac_sha256_hex}
   Key: Retell API key. Timestamp must be within 5 minutes.
   Reference: https://docs.retellai.com/features/secure-webhook
"""

import hashlib
import hmac
import json
import logging
import re
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.call import CallSession
from app.models.contractor import Contractor
from app.models.lead import Lead
from app.services.billing import BillingService
from app.services.call_events import broadcast_call_event
from app.services.claude_agent import ClaudeAgent
from app.services.sms import SMSService

router = APIRouter(tags=["retell"])
logger = logging.getLogger(__name__)

# In-memory registry of active ClaudeAgent instances keyed by Retell call_id.
# Replace with Redis in production for horizontal scaling / restart safety.
_active_agents: dict[str, ClaudeAgent] = {}

# Transfer numbers requested during this call (keyed by call_id).
# Set by the transfer_call tool; consumed in the next WebSocket response.
_pending_transfers: dict[str, str] = {}


# ---------------------------------------------------------------------------
# WebSocket — Retell Custom LLM endpoint
# ---------------------------------------------------------------------------

@router.websocket("/llm-websocket/{call_id}")
async def llm_websocket(
    websocket: WebSocket,
    call_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Retell connects here when a call starts (Custom LLM agent type).

    Retell → server message types:
      ping_pong       — heartbeat every 2s; must respond within 5s
      call_details    — call metadata (only if requested in config event)
      response_required — user finished speaking; send Claude's response
      reminder_required — user silent too long; send nudge
      update_only     — mid-speech transcript update; no response needed

    Server → Retell message types:
      config          — sent immediately on open to configure the session
      ping_pong       — heartbeat reply (must echo the timestamp)
      response        — agent text to speak; carries response_id, content,
                        content_complete, end_call, transfer_number
      update_agent    — modify responsiveness/interruption_sensitivity mid-call
    """
    await websocket.accept()
    logger.info("WebSocket opened | call_id=%s", call_id)

    # Restore agent from registry on WebSocket reconnect
    agent: ClaudeAgent | None = _active_agents.get(call_id)
    call_session: CallSession | None = agent.call_session if agent else None

    # Send config event immediately so Retell knows to:
    # - send call_details on first message
    # - enable ping_pong heartbeat
    await websocket.send_text(json.dumps({
        "response_type": "config",
        "config": {
            "auto_reconnect": True,
            "call_details": True,
        },
    }))

    try:
        while True:
            raw = await websocket.receive_text()
            data: dict = json.loads(raw)
            interaction_type: str = data.get("interaction_type", "")

            # ------------------------------------------------------------------
            # Ping-pong heartbeat — must respond within 5 seconds
            # ------------------------------------------------------------------
            if interaction_type == "ping_pong":
                await websocket.send_text(json.dumps({
                    "response_type": "ping_pong",
                    "timestamp": data.get("timestamp", int(time.time() * 1000)),
                }))
                continue

            # ------------------------------------------------------------------
            # call_details — first real message; initialise agent + send greeting
            # ------------------------------------------------------------------
            elif interaction_type == "call_details":
                call_info: dict = data.get("call", {})
                to_number: str = call_info.get("to_number", "")
                from_number: str = call_info.get("from_number", "")

                contractor = await _get_contractor_by_phone(to_number, db)

                call_session = CallSession(
                    retell_call_id=call_id,
                    contractor_id=contractor.id,
                    status="active",
                    conversation_history=[],
                    started_at=datetime.now(tz=timezone.utc),
                )
                db.add(call_session)
                await db.flush()

                # Check call usage limit before starting the session
                usage = await BillingService().check_usage_limit(contractor, "calls")
                if not usage["allowed"]:
                    logger.warning(
                        "Call limit reached | contractor=%s used=%d limit=%d",
                        contractor.name, usage["used"], usage["limit"],
                    )
                    await websocket.send_text(json.dumps({
                        "response_type": "response",
                        "response_id": 0,
                        "content": (
                            "We're sorry, this account has reached its monthly call limit. "
                            "Please contact your service provider."
                        ),
                        "content_complete": True,
                        "end_call": True,
                    }))
                    return

                agent = ClaudeAgent(
                    contractor=contractor,
                    call_session=call_session,
                    db=db,
                )
                _active_agents[call_id] = agent

                await broadcast_call_event({
                    "type": "call_started",
                    "call_id": call_id,
                    "contractor_name": contractor.name,
                    "from_number": from_number,
                    "to_number": to_number,
                    "started_at": call_session.started_at.isoformat(),
                })

                # Opening greeting — response_id 0 for the first agent turn
                greeting = await agent.process_turn("__call_started__")
                await websocket.send_text(json.dumps({
                    "response_type": "response",
                    "response_id": 0,
                    "content": greeting,
                    "content_complete": True,
                    "end_call": False,
                }))
                logger.info(
                    "Agent initialised | call_id=%s contractor=%s from=%s",
                    call_id, contractor.name, from_number,
                )

            # ------------------------------------------------------------------
            # response_required / reminder_required — run Claude, send reply
            # ------------------------------------------------------------------
            elif interaction_type in ("response_required", "reminder_required"):
                response_id: int = data.get("response_id", 0)
                transcript: list[dict] = data.get("transcript", [])
                user_message = _latest_user_utterance(transcript)

                if agent is None:
                    agent = await _rebuild_agent(call_id, db)
                    _active_agents[call_id] = agent

                if not user_message and interaction_type == "reminder_required":
                    await websocket.send_text(json.dumps({
                        "response_type": "response",
                        "response_id": response_id,
                        "content": "Are you still there? I'm here to help.",
                        "content_complete": True,
                        "end_call": False,
                    }))
                    continue

                response_text = await agent.process_turn(user_message)

                # Check if the transfer_call tool fired during this turn
                transfer_number = _pending_transfers.pop(call_id, None)
                end_call = bool(transfer_number) or _should_end_call(agent)

                payload: dict = {
                    "response_type": "response",
                    "response_id": response_id,
                    "content": response_text,
                    "content_complete": True,
                    "end_call": end_call,
                }
                if transfer_number:
                    payload["transfer_number"] = transfer_number
                    logger.info("Transfer initiated | call_id=%s to=%s", call_id, transfer_number)

                await websocket.send_text(json.dumps(payload))
                logger.info(
                    "Turn | call_id=%s response_id=%d end_call=%s",
                    call_id, response_id, end_call,
                )

                # Broadcast transcript update to dashboard clients
                lead_score: dict = {}
                if agent and agent.call_session and agent.call_session.lead_id:
                    cs = agent.call_session
                    lead_score = {
                        "emergency": getattr(cs, "emergency_score", None),
                        "revenue": getattr(cs, "revenue_score", None),
                        "close": getattr(cs, "close_probability", None),
                    }
                await broadcast_call_event({
                    "type": "transcript_update",
                    "call_id": call_id,
                    "role": "agent",
                    "content": response_text,
                    "lead_score": lead_score,
                })

            # ------------------------------------------------------------------
            # update_only — transcript update mid-speech; no response needed
            # ------------------------------------------------------------------
            elif interaction_type == "update_only":
                logger.debug("update_only | call_id=%s", call_id)

            else:
                logger.warning("Unknown interaction_type=%s | call_id=%s", interaction_type, call_id)

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected | call_id=%s", call_id)
        await _finalise_session(call_id, {}, db)

    except Exception as exc:
        logger.exception("Unhandled error in WebSocket | call_id=%s error=%s", call_id, exc)
        try:
            await websocket.send_text(json.dumps({
                "response_type": "response",
                "response_id": 0,
                "content": "I'm sorry, I'm experiencing a technical issue. Please hold.",
                "content_complete": True,
                "end_call": False,
            }))
        except Exception:
            pass

    finally:
        _active_agents.pop(call_id, None)
        _pending_transfers.pop(call_id, None)


# ---------------------------------------------------------------------------
# Inbound call routing — Retell calls this when phone receives an inbound call
# Must return {"agent_id": "..."} to tell Retell which agent to use
# ---------------------------------------------------------------------------

@router.post("/retell/inbound")
async def retell_inbound(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Called by Retell when an inbound call arrives on the phone number.
    Looks up the contractor by their assigned phone number and returns
    the correct agent_id so Retell routes to the right AI agent.
    """
    from app.models.contractor import Contractor

    try:
        payload = await request.json()
    except Exception:
        payload = {}

    to_number: str = payload.get("to_number", "")
    from_number: str = payload.get("from_number", "")
    logger.info("Retell inbound | to=%s from=%s", to_number, from_number)

    # Look up contractor by their Retell phone number
    result = await db.execute(
        select(Contractor).where(
            Contractor.phone_number == to_number,
            Contractor.is_active == True,  # noqa: E712
        )
    )
    contractor = result.scalar_one_or_none()

    if contractor and contractor.retell_agent_id:
        logger.info(
            "Routing inbound call to agent %s for contractor %s",
            contractor.retell_agent_id, contractor.name,
        )
        return {"agent_id": contractor.retell_agent_id}

    # Fallback: use the first active agent found
    logger.warning("No contractor found for number %s — using fallback agent", to_number)
    fallback_result = await db.execute(
        select(Contractor).where(
            Contractor.retell_agent_id.isnot(None),
            Contractor.is_active == True,  # noqa: E712
        ).limit(1)
    )
    fallback = fallback_result.scalar_one_or_none()
    if fallback and fallback.retell_agent_id:
        return {"agent_id": fallback.retell_agent_id}

    raise HTTPException(status_code=404, detail="No agent configured for this number")


# ---------------------------------------------------------------------------
# HTTP Webhook — Retell lifecycle events
# ---------------------------------------------------------------------------

@router.post("/retell/webhook")
async def retell_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Unified Retell webhook for call lifecycle events.

    Events handled:
      call_started    — informational; CallSession created by WebSocket handler
      call_ended      — finalise session, update Lead, schedule post-call jobs
      call_analyzed   — apply Retell's post-call analysis to Lead record
      transfer_*      — log transfer lifecycle events
      transcript_updated — optional real-time transcript sync
    """
    raw_body = await request.body()
    _verify_retell_signature(request, raw_body)

    payload: dict = json.loads(raw_body)
    event: str = payload.get("event", "")
    call_info: dict = payload.get("call", {})
    call_id: str = call_info.get("call_id", "")

    logger.info("Retell webhook | event=%s call_id=%s", event, call_id)

    if event == "call_started":
        pass  # CallSession is created in the WebSocket handler

    elif event == "call_ended":
        await _finalise_session(call_id, call_info, db)
        await _schedule_post_call_jobs(call_id, db)

        # Missed call recovery SMS — fire if AI didn't really answer
        call_status: str = call_info.get("call_status", "")
        start_ts = call_info.get("start_timestamp", 0)
        end_ts = call_info.get("end_timestamp", 0)
        duration_s = (end_ts - start_ts) // 1000 if (start_ts and end_ts) else 0
        from_number_wh: str = call_info.get("from_number", "")
        to_number_wh: str = call_info.get("to_number", "")
        if from_number_wh and to_number_wh and (call_status == "error" or duration_s < 10):
            try:
                _contractor_r = await db.execute(
                    select(Contractor).where(
                        Contractor.phone_number == to_number_wh,
                        Contractor.is_active.is_(True),
                    )
                )
                _wh_contractor = _contractor_r.scalar_one_or_none()
                if _wh_contractor:
                    import asyncio as _asyncio
                    from app.services.missed_call import send_missed_call_sms
                    _asyncio.create_task(
                        send_missed_call_sms(
                            to_number=from_number_wh,
                            contractor_name=_wh_contractor.name,
                            ai_number=to_number_wh,
                        )
                    )
            except Exception as _exc:
                logger.warning("Missed call SMS dispatch error | %s", _exc)

    elif event == "call_analyzed":
        await _apply_analysis(call_id, call_info, db)
        transcript = payload.get("transcript", "")
        if transcript:
            result = await db.execute(select(CallSession).where(CallSession.retell_call_id == call_id))
            call_session = result.scalar_one_or_none()
            if call_session:
                contractor_result = await db.execute(
                    select(Contractor).where(Contractor.id == call_session.contractor_id)
                )
                contractor = contractor_result.scalar_one_or_none()
                if contractor:
                    from app.services.post_call import PostCallAnalyser
                    analyser = PostCallAnalyser()
                    await analyser.analyse(call_session, transcript, contractor, db)

    elif event in ("transfer_started", "transfer_bridged", "transfer_cancelled", "transfer_ended"):
        logger.info(
            "Transfer event | event=%s call_id=%s destination=%s",
            event, call_id,
            payload.get("transfer_destination", {}).get("number", ""),
        )

    elif event == "transcript_updated":
        pass  # Handled real-time in the WebSocket

    else:
        logger.warning("Unknown Retell event=%s | call_id=%s", event, call_id)

    return {"status": "ok"}


# ---------------------------------------------------------------------------
# HTTP Webhook — Missed call
# ---------------------------------------------------------------------------

@router.post("/retell/missed-call")
async def missed_call(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Fires when an inbound call goes unanswered.
    Sends immediate SMS, creates a partial Lead, schedules outbound recovery.
    """
    raw_body = await request.body()
    _verify_retell_signature(request, raw_body)
    payload: dict = json.loads(raw_body)

    call_id: str = payload.get("call_id", "")
    from_number: str = payload.get("from_number", "")
    to_number: str = payload.get("to_number", "")

    contractor = await _get_contractor_by_phone(to_number, db)

    lead = Lead(
        contractor_id=contractor.id,
        call_id=call_id,
        phone=from_number,
        call_direction="missed_call_recovery",
        lead_source="retell_missed_call",
        appointment_status="callback_required",
    )
    db.add(lead)
    await db.flush()

    if contractor.sms_enabled and from_number:
        sms = SMSService(contractor)
        result = sms.send_missed_call_recovery(phone=from_number)
        if result.get("success"):
            await BillingService().increment_usage(contractor, "sms", db)
        lead.sms_confirmation_sent = True

    try:
        from app.services.scheduler import schedule_missed_call_recovery
        schedule_missed_call_recovery(
            contractor_id=str(contractor.id),
            to_number=from_number,
            from_number=to_number,
            lead_id=str(lead.id),
        )
    except ImportError:
        logger.debug("Scheduler not wired — skipping missed call recovery scheduling.")

    logger.info("Missed call | call_id=%s from=%s contractor=%s", call_id, from_number, contractor.name)
    return {"status": "recovery_initiated", "lead_id": str(lead.id)}


# ---------------------------------------------------------------------------
# Internal: called by transfer_call tool to queue a transfer on next WS send
# ---------------------------------------------------------------------------

def queue_transfer(call_id: str, transfer_number: str) -> None:
    """
    Called by the transfer_call tool handler.
    The transfer_number is injected into the next WebSocket response payload
    so Retell bridges the call — no separate REST API call needed.
    """
    _pending_transfers[call_id] = transfer_number
    logger.info("Transfer queued | call_id=%s to=%s", call_id, transfer_number)


# ---------------------------------------------------------------------------
# Signature verification (Retell secure webhook)
# ---------------------------------------------------------------------------

_SIG_RE = re.compile(r"v=(\d+),d=([0-9a-f]+)")

def _verify_retell_signature(request: Request, raw_body: bytes) -> None:
    """
    Verify the x-retell-signature header.

    Format:  v={timestamp_ms},d={hmac_sha256_hex}
    Key:     Retell API key (the one with a webhook badge in the dashboard)
    Data:    raw_body_string + timestamp_string   (concatenated, no separator)
    Window:  timestamp must be within 5 minutes of now
    """
    header = request.headers.get("x-retell-signature", "")
    match = _SIG_RE.fullmatch(header)
    if not match:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing or malformed x-retell-signature header.",
        )

    timestamp_ms = int(match.group(1))
    received_digest = match.group(2)

    # Timestamp freshness check — reject if older than 5 minutes
    now_ms = int(time.time() * 1000)
    if abs(now_ms - timestamp_ms) > 5 * 60 * 1000:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Retell signature timestamp is stale.",
        )

    # HMAC-SHA256(raw_body + timestamp_string, api_key)
    signing_data = raw_body + str(timestamp_ms).encode()
    expected_digest = hmac.new(
        settings.retell_api_key.encode(),
        signing_data,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(received_digest, expected_digest):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid Retell signature.",
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _get_contractor_by_phone(to_number: str, db: AsyncSession) -> Contractor:
    result = await db.execute(
        select(Contractor).where(
            Contractor.phone_number == to_number,
            Contractor.is_active.is_(True),
        )
    )
    contractor = result.scalar_one_or_none()
    if contractor is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No active contractor found for number {to_number}.",
        )
    return contractor


async def _rebuild_agent(call_id: str, db: AsyncSession) -> ClaudeAgent:
    """Reconstruct a ClaudeAgent from DB state on a cold start."""
    session_result = await db.execute(
        select(CallSession).where(CallSession.retell_call_id == call_id)
    )
    call_session = session_result.scalar_one_or_none()
    if call_session is None:
        raise HTTPException(status_code=404, detail=f"No CallSession for call_id={call_id}.")
    contractor_result = await db.execute(
        select(Contractor).where(Contractor.id == call_session.contractor_id)
    )
    contractor = contractor_result.scalar_one_or_none()
    if contractor is None:
        raise HTTPException(status_code=404, detail="Contractor not found.")
    return ClaudeAgent(contractor=contractor, call_session=call_session, db=db)


async def _finalise_session(call_id: str, call_info: dict, db: AsyncSession) -> None:
    """Mark CallSession complete; stamp media URLs onto the Lead."""
    result = await db.execute(select(CallSession).where(CallSession.retell_call_id == call_id))
    call_session = result.scalar_one_or_none()
    if not call_session:
        return

    # Increment call usage for the contractor
    contractor_result = await db.execute(
        select(Contractor).where(Contractor.id == call_session.contractor_id)
    )
    _contractor = contractor_result.scalar_one_or_none()
    if _contractor:
        await BillingService().increment_usage(_contractor, "calls", db)

    call_session.status = "completed"
    call_session.ended_at = datetime.now(tz=timezone.utc)

    start_ts = call_info.get("start_timestamp", 0)
    end_ts = call_info.get("end_timestamp", 0)
    if start_ts and end_ts:
        call_session.duration_seconds = (end_ts - start_ts) // 1000

    if call_session.lead_id:
        lead_result = await db.execute(select(Lead).where(Lead.id == call_session.lead_id))
        lead = lead_result.scalar_one_or_none()
        if lead:
            if call_info.get("recording_url"):
                lead.recording_url = call_info["recording_url"]
            if call_info.get("public_log_url"):
                lead.transcript_url = call_info["public_log_url"]
            if call_info.get("transcript"):
                lead.raw_transcript = call_info["transcript"]

    await db.flush()
    logger.info("Session finalised | call_id=%s duration=%ss", call_id, call_session.duration_seconds)

    await broadcast_call_event({
        "type": "call_ended",
        "call_id": call_id,
        "duration_seconds": call_session.duration_seconds,
    })


async def _apply_analysis(call_id: str, call_info: dict, db: AsyncSession) -> None:
    """Apply Retell's post-call analysis to the Lead record."""
    result = await db.execute(select(CallSession).where(CallSession.retell_call_id == call_id))
    call_session = result.scalar_one_or_none()
    if not call_session or not call_session.lead_id:
        return

    lead_result = await db.execute(select(Lead).where(Lead.id == call_session.lead_id))
    lead = lead_result.scalar_one_or_none()
    if not lead:
        return

    analysis: dict = call_info.get("call_analysis") or {}
    if analysis.get("call_summary"):
        lead.notes = (lead.notes or "") + f"\n[Retell summary] {analysis['call_summary']}"
    if analysis.get("user_sentiment"):
        lead.customer_sentiment = analysis["user_sentiment"]

    await db.flush()


async def _schedule_post_call_jobs(call_id: str, db: AsyncSession) -> None:
    """Queue APScheduler jobs after a call ends."""
    try:
        from app.services.scheduler import (
            schedule_appointment_reminder,
            schedule_review_request,
            schedule_unbooked_followup,
        )
    except ImportError:
        return

    result = await db.execute(select(CallSession).where(CallSession.retell_call_id == call_id))
    call_session = result.scalar_one_or_none()
    if not call_session or not call_session.lead_id:
        return

    lead_result = await db.execute(select(Lead).where(Lead.id == call_session.lead_id))
    lead = lead_result.scalar_one_or_none()
    if not lead or not lead.phone:
        return

    contractor_id = str(lead.contractor_id)
    lead_id = str(lead.id)

    if lead.appointment_status == "booked" and lead.appointment_time:
        schedule_appointment_reminder(
            contractor_id=contractor_id,
            lead_id=lead_id,
            phone=lead.phone,
            appointment_time=lead.appointment_time,
            service_address=lead.service_address or "",
        )
        schedule_review_request(
            contractor_id=contractor_id,
            lead_id=lead_id,
            phone=lead.phone,
            appointment_time=lead.appointment_time,
        )
    elif lead.appointment_status == "not_booked":
        schedule_unbooked_followup(
            contractor_id=contractor_id,
            lead_id=lead_id,
            phone=lead.phone,
        )


def _latest_user_utterance(transcript: list[dict]) -> str:
    """Return the most recent user turn content from a Retell transcript."""
    for turn in reversed(transcript):
        if turn.get("role") == "user":
            return turn.get("content", "").strip()
    return ""


def _should_end_call(agent: ClaudeAgent) -> bool:
    """True if the last tool call was transfer_call with urgency=immediate."""
    for msg in reversed(agent.call_session.conversation_history):
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if (
                isinstance(block, dict)
                and block.get("type") == "tool_use"
                and block.get("name") == "transfer_call"
                and block.get("input", {}).get("urgency") == "immediate"
            ):
                return True
    return False
