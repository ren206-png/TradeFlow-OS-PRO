from __future__ import annotations
"""
Retell REST API v2 client.

Base URL:     https://api.retellai.com
Auth:         Authorization: Bearer {RETELL_API_KEY}
Docs:         https://docs.retellai.com/api-references/

Key endpoints used by TradeFlow-OS Pro:
  POST   /v2/create-phone-call         outbound calls / missed-call recovery
  POST   /v2/register-phone-call       custom telephony inbound registration
  PATCH  /v2/update-live-call/{id}     inject context into an active call
  POST   /create-agent                 provision Custom LLM agent
  PUT    /update-agent/{id}            update agent config
  GET    /v2/get-call/{id}             fetch call details + transcript
"""

import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_BASE = "https://api.retellai.com"


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.retell_api_key}",
        "Content-Type": "application/json",
    }


class RetellClient:

    # ------------------------------------------------------------------
    # Phone calls
    # ------------------------------------------------------------------

    async def create_phone_call(
        self,
        to_number: str,
        from_number: str,
        override_agent_id: str | None = None,
        metadata: dict | None = None,
        retell_llm_dynamic_variables: dict | None = None,
    ) -> dict:
        """
        POST /v2/create-phone-call
        Initiate an outbound phone call.
        `override_agent_id` selects the agent for this call only.
        """
        payload: dict = {
            "to_number": to_number,
            "from_number": from_number,
        }
        if override_agent_id:
            payload["override_agent_id"] = override_agent_id
        if metadata:
            payload["metadata"] = metadata
        if retell_llm_dynamic_variables:
            payload["retell_llm_dynamic_variables"] = retell_llm_dynamic_variables

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{_BASE}/v2/create-phone-call",
                json=payload,
                headers=_headers(),
                timeout=10.0,
            )
            response.raise_for_status()
            return response.json()

    async def register_phone_call(
        self,
        agent_id: str,
        from_number: str | None = None,
        to_number: str | None = None,
        direction: str = "inbound",
        metadata: dict | None = None,
    ) -> dict:
        """
        POST /v2/register-phone-call
        Register an inbound call before bridging to the WebSocket.
        Used for custom telephony integrations.
        Returns a call_id that becomes the WebSocket path parameter.
        """
        payload: dict = {"agent_id": agent_id, "direction": direction}
        if from_number:
            payload["from_number"] = from_number
        if to_number:
            payload["to_number"] = to_number
        if metadata:
            payload["metadata"] = metadata

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{_BASE}/v2/register-phone-call",
                json=payload,
                headers=_headers(),
                timeout=10.0,
            )
            response.raise_for_status()
            return response.json()

    async def update_live_call(
        self,
        call_id: str,
        additional_context: str | None = None,
        trigger_response: bool = False,
        metadata: dict | None = None,
    ) -> dict:
        """
        PATCH /v2/update-live-call/{call_id}
        Inject additional context or trigger an agent nudge mid-call.
        `additional_context` is injected into the transcript and agent context.
        `trigger_response` interrupts current speech or nudges a waiting agent.
        """
        fields: dict = {}
        if additional_context is not None:
            fields["additional_context"] = additional_context
        if trigger_response:
            fields["trigger_response"] = trigger_response
        if metadata is not None:
            fields["metadata"] = metadata

        payload: dict = {}
        if fields:
            payload["call_control"] = {k: v for k, v in fields.items()
                                        if k in ("trigger_response", "additional_context")}
            if metadata is not None:
                payload["fields_to_override"] = {"metadata": metadata}

        try:
            async with httpx.AsyncClient() as client:
                response = await client.patch(
                    f"{_BASE}/v2/update-live-call/{call_id}",
                    json=payload,
                    headers=_headers(),
                    timeout=10.0,
                )
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as exc:
            logger.error("update_live_call failed | call_id=%s error=%s", call_id, exc.response.text)
            return {"success": False, "error": str(exc)}

    async def get_call(self, call_id: str) -> dict:
        """
        GET /v2/get-call/{call_id}
        Fetch full call details including transcript and post-call analysis.
        """
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{_BASE}/v2/get-call/{call_id}",
                headers=_headers(),
                timeout=10.0,
            )
            response.raise_for_status()
            return response.json()

    async def stop_call(self, call_id: str) -> dict:
        """
        POST /v2/stop-call
        Hang up an active call programmatically.
        """
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{_BASE}/v2/stop-call",
                    json={"call_id": call_id},
                    headers=_headers(),
                    timeout=10.0,
                )
                response.raise_for_status()
                return {"success": True}
        except httpx.HTTPStatusError as exc:
            logger.error("stop_call failed | call_id=%s error=%s", call_id, exc.response.text)
            return {"success": False, "error": str(exc)}

    # ------------------------------------------------------------------
    # Agent management
    # ------------------------------------------------------------------

    async def create_agent(self, agent_config: dict) -> dict:
        """
        POST /create-agent
        Create a Custom LLM agent. `response_engine.type` must be "custom_llm"
        and `response_engine.llm_websocket_url` points at /llm-websocket/{call_id}.
        Required: response_engine, voice_id.
        """
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{_BASE}/create-agent",
                json=agent_config,
                headers=_headers(),
                timeout=10.0,
            )
            response.raise_for_status()
            return response.json()

    async def update_agent(self, agent_id: str, agent_config: dict) -> dict:
        """
        PUT /update-agent/{agent_id}
        Update an existing agent's configuration.
        """
        async with httpx.AsyncClient() as client:
            response = await client.put(
                f"{_BASE}/update-agent/{agent_id}",
                json=agent_config,
                headers=_headers(),
                timeout=10.0,
            )
            response.raise_for_status()
            return response.json()

    async def get_agent(self, agent_id: str) -> dict:
        """GET /get-agent/{agent_id}"""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{_BASE}/get-agent/{agent_id}",
                headers=_headers(),
                timeout=10.0,
            )
            response.raise_for_status()
            return response.json()
