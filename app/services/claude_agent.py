import asyncio
import json
import re
import logging

import anthropic
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.call import CallSession
from app.models.contractor import Contractor
from app.prompts.builder import build_system_prompt, build_system_prompt_async
from app.services.triage import get_urgency_tool_schema
from app.tools.definitions import TRADEFLOW_TOOLS
from app.tools.handlers import execute_tool

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 8


class ClaudeAgent:
    """Stateful conversation engine for one call session."""

    def __init__(
        self,
        contractor: Contractor,
        call_session: CallSession,
        db: AsyncSession,
        intake_section: str = "",
    ) -> None:
        self.contractor = contractor
        self.call_session = call_session
        self.db = db
        # Synchronous base prompt — triage injection happens async in initialise()
        self.system_prompt = build_system_prompt(contractor, intake_section=intake_section)
        self._intake_section = intake_section
        self._client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        self._tool_context = {
            "contractor": contractor,
            "call_session": call_session,
            "db": db,
        }

    async def initialise_async_prompt(self) -> None:
        """
        Call once after construction to inject triage section when triage_library_v2 is ON.
        Safe to skip — falls back to sync-built prompt.
        """
        try:
            self.system_prompt = await build_system_prompt_async(
                self.contractor,
                intake_section=self._intake_section,
                db=self.db,
            )
        except Exception as exc:
            logger.warning(
                "ClaudeAgent: async prompt init failed, using base prompt | err=%s", exc
            )

    async def process_turn(self, user_message: str) -> str:
        """
        Process one conversation turn and return the agent's spoken response.

        Pass user_message="__call_started__" on the first turn to generate the
        opening greeting without adding a fake user message to history.

        1. Append user message to history (unless it's the sentinel).
        2. Call Claude with full history, system prompt, and tool definitions.
        3. Run the agentic tool loop until no tool_use blocks remain.
        4. Persist updated history to CallSession.
        5. Return the final text for Retell to speak.
        """
        messages: list[dict] = list(self.call_session.conversation_history)

        # The Messages API requires the conversation to end with a non-empty user turn.
        if user_message == "__call_started__":
            if not messages:
                messages.append({"role": "user", "content": "[The phone call has just connected. Greet the caller now.]"})
        elif user_message.strip():
            messages.append({"role": "user", "content": user_message})
        if not messages or messages[-1]["role"] != "user":
            messages.append({"role": "user", "content": "[The caller hasn't said anything new. Briefly check they're still there.]"})

        iteration = 0
        while iteration < MAX_TOOL_ITERATIONS:
            response = await self._call_claude(messages)
            has_tool_calls = any(block.type == "tool_use" for block in response.content)

            if has_tool_calls:
                messages = await self._handle_tool_calls(response, messages)
                iteration += 1
            else:
                break
        else:
            logger.warning(
                "Reached max tool iterations (%d) for call %s",
                MAX_TOOL_ITERATIONS,
                self.call_session.retell_call_id,
            )

        # Extract final text response
        text_response = _extract_text(response)
        if not text_response:
            # Never send dead air: Claude sometimes ends a turn with only a tool call.
            transferring = False
            for m in reversed(messages):  # this turn = everything after the caller's last words
                if m["role"] == "user" and isinstance(m["content"], str):
                    break
                if isinstance(m["content"], list) and any(
                    b.get("type") == "tool_use" and b.get("name") == "transfer_call" for b in m["content"]
                ):
                    transferring = True
                    break
            text_response = ("Let me connect you with someone now." if transferring
                             else "Sorry, could you say that one more time?")

        # Append final assistant turn — use full content list to preserve tool blocks
        # and avoid sending empty string content which the API rejects
        final_content = _serialize_content(response.content) if response.content else [{"type": "text", "text": text_response or " "}]
        messages.append({"role": "assistant", "content": final_content})

        # Persist to DB
        self.call_session.conversation_history = messages
        await self.db.flush()

        return text_response

    async def _call_claude(self, messages: list[dict]) -> anthropic.types.Message:
        """Send the current conversation to Claude and return the raw Message.

        Retries up to 3 attempts with exponential backoff (1s, 2s, 4s) on
        Anthropic RateLimitError (429) and APIStatusError with status 529.
        """
        delays = [1, 2, 4]
        last_exc: Exception | None = None
        for attempt, delay in enumerate(delays, start=1):
            try:
                tools = list(TRADEFLOW_TOOLS)
                if settings.emergency_triage:
                    tools.append(get_urgency_tool_schema())
                if settings.safety_coaching:
                    from app.tools.deliver_coaching import get_deliver_coaching_tool_schema
                    tools.append(get_deliver_coaching_tool_schema())
                # Phase 6: commercial intake tool — gated behind flag
                if settings.commercial_intake:
                    from app.tools.commercial_intake_tool import get_collect_commercial_intake_tool_schema
                    tools.append(get_collect_commercial_intake_tool_schema())
                return await self._client.messages.create(
                    model=settings.claude_model,
                    max_tokens=settings.claude_max_tokens,
                    system=self.system_prompt,
                    tools=tools,
                    messages=messages,
                )
            except anthropic.RateLimitError as exc:
                last_exc = exc
                logger.warning("Claude rate-limited (attempt %d/3); retrying in %ds", attempt, delay)
            except anthropic.APIStatusError as exc:
                if exc.status_code == 529:
                    last_exc = exc
                    logger.warning("Claude overloaded 529 (attempt %d/3); retrying in %ds", attempt, delay)
                else:
                    raise
            if attempt < len(delays):
                await asyncio.sleep(delay)
        raise last_exc

    async def _handle_tool_calls(
        self, response: anthropic.types.Message, messages: list[dict]
    ) -> list[dict]:
        """
        Execute every tool_use block in the response, collect results, and
        return the updated messages list ready for the next Claude call.
        """
        # Append the full assistant message (may include text + tool_use blocks)
        assistant_content = _serialize_content(response.content)
        messages.append({"role": "assistant", "content": assistant_content})

        # Build tool_result blocks for every tool_use
        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue

            logger.info("Executing tool: %s | input: %s", block.name, block.input)
            result = await execute_tool(block.name, block.input, self._tool_context)

            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result),
                }
            )

        messages.append({"role": "user", "content": tool_results})
        return messages


def _extract_text(response: anthropic.types.Message) -> str:
    """Pull the first text block out of a Claude response."""
    for block in response.content:
        if block.type == "text":
            return to_spoken_text(block.text)
    return ""


_EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F]")


def to_spoken_text(text: str) -> str:
    """Make model output safe for text-to-speech: no markdown, list markers, emoji or wrapping quotes."""
    t = _EMOJI.sub("", text or "")
    t = re.sub(r"[*_`#]+", "", t)
    t = re.sub(r"^\s*(?:[-•]|\d+[.)])\s+", "", t, flags=re.MULTILINE)
    lines = [ln.strip() for ln in t.splitlines() if ln.strip()]
    joined = ""
    for ln in lines:
        if joined and not joined.endswith((".", "?", "!", ",", ":", ";")):
            joined += ","
        joined = f"{joined} {ln}".strip()
    return joined.strip().strip('"\u201c\u201d').strip()


def _serialize_content(content: list) -> list[dict]:
    """Convert Anthropic SDK content blocks to plain dicts for JSON storage."""
    serialized = []
    for block in content:
        if block.type == "text":
            serialized.append({"type": "text", "text": block.text})
        elif block.type == "tool_use":
            serialized.append(
                {
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                }
            )
    return serialized
