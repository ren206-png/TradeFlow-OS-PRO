"""Streaming replies: sentences are spoken as soon as they exist; fallbacks, retries and flags still behave."""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.models.call import CallSession
from app.services.claude_agent import ClaudeAgent, SentenceChunker


def test_chunker_splits_on_sentences_but_not_on_abbreviations_or_addresses():
    ch = SentenceChunker()
    text = ("A dripping faucet is something we can take care of. Your address is 123 Test St. NW in Edmonton, right? "
            "Let me check T5J 1A1. Okay. One moment please")
    out = []
    for i in range(0, len(text), 7):          # arrives in small pieces
        out += ch.feed(text[i:i + 7])
    out.append(ch.flush())
    assert out[0] == "A dripping faucet is something we can take care of."
    assert any("123 Test St. NW" in s for s in out)            # not cut after "St."
    assert " ".join(out).replace("  ", " ") == text            # nothing lost or duplicated


class _FakeStream:
    def __init__(self, deltas, final):
        self._deltas, self._final = deltas, final
        self.text_stream = self._gen()

    async def _gen(self):
        for d in self._deltas:
            yield d

    async def get_final_message(self):
        return self._final

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _final(text="", tool=None):
    blocks = []
    if text:
        blocks.append(SimpleNamespace(type="text", text=text))
    if tool:
        blocks.append(SimpleNamespace(type="tool_use", id="t1", name=tool, input={}))
    return SimpleNamespace(content=blocks)


def _agent(db, contractor, streams):
    cs = CallSession(id=uuid.uuid4(), retell_call_id="c-stream", contractor_id=contractor.id, status="active", conversation_history=[])
    agent = ClaudeAgent(contractor=contractor, call_session=cs, db=db)
    client = MagicMock()
    client.messages.stream = MagicMock(side_effect=streams)
    agent._client = client
    return agent


@pytest.mark.asyncio
async def test_first_sentence_is_spoken_before_the_reply_finishes(db, contractor):
    text = "I can help with that right now. Can I get your address?"
    agent = _agent(db, contractor, [_FakeStream(["I can help with ", "that right now. Can I get", " your address?"], _final(text))])
    spoken = []

    async def on_text(chunk):
        spoken.append(chunk)

    result = await agent.process_turn("my sink is leaking", on_text=on_text)
    assert spoken == ["I can help with that right now.", " Can I get your address?"]
    assert result == text


@pytest.mark.asyncio
async def test_tools_only_turn_still_speaks_something(db, contractor):
    agent = _agent(db, contractor, [_FakeStream([], _final("", tool="end_call")), _FakeStream([], _final(""))])
    spoken = []

    async def on_text(chunk):
        spoken.append(chunk)

    await agent.process_turn("goodbye", on_text=on_text)
    assert spoken and spoken[0]          # the closing line, never dead air


@pytest.mark.asyncio
async def test_non_streaming_path_is_unchanged(db, contractor):
    from unittest.mock import AsyncMock
    agent = _agent(db, contractor, [])
    agent._call_claude = AsyncMock(return_value=_final("Hello there."))
    assert await agent.process_turn("hi") == "Hello there."
