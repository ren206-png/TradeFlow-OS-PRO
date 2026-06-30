import json
import logging

import anthropic
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.call import CallSession
from app.models.contractor import Contractor
from app.prompts.builder import build_system_prompt
from app.tools.definitions import TRADEFLOW_TOOLS
from app.tools.handlers import execute_tool

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 5


class ClaudeAgent:
    """Stateful conversation engine for one call session."""

    def __init__(self, contractor: Contractor, call_session: CallSession, db: AsyncSession) -> None:
        self.contractor = contractor
        self.call_session = call_session
        self.db = db
        self.system_prompt = build_system_prompt(contractor)
        self._client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        self._tool_context = {
            "contractor": contractor,
            "call_session": call_session,
            "db": db,
        }

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

        if user_message != "__call_started__":
            messages.append({"role": "user", "content": user_message})

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

        # Append final assistant turn — use full content list to preserve tool blocks
        # and avoid sending empty string content which the API rejects
        final_content = _serialize_content(response.content) if response.content else [{"type": "text", "text": text_response or " "}]
        messages.append({"role": "assistant", "content": final_content})

        # Persist to DB
        self.call_session.conversation_history = messages
        await self.db.flush()

        return text_response

    async def _call_claude(self, messages: list[dict]) -> anthropic.types.Message:
        """Send the current conversation to Claude and return the raw Message."""
        return await self._client.messages.create(
            model=settings.claude_model,
            max_tokens=settings.claude_max_tokens,
            system=self.system_prompt,
            tools=TRADEFLOW_TOOLS,
            messages=messages,
        )

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
            return block.text
    return ""


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
