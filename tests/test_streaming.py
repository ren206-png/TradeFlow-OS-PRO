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

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for d in self._deltas:
            if isinstance(d, str):
                yield SimpleNamespace(type="text", text=d)
            else:   # ("tool", name): a tool_use block starts
                yield SimpleNamespace(type="content_block_start", content_block=SimpleNamespace(type="tool_use", name=d[1]))

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


@pytest.mark.asyncio
async def test_slow_tool_with_no_speech_gets_a_one_moment_line_but_quick_tools_do_not(db, contractor):
    slow = _agent(db, contractor, [_FakeStream([("tool", "check_availability")], _final("", tool="check_availability")),
                                   _FakeStream(["I have 8 AM or 10 AM."], _final("I have 8 AM or 10 AM."))])
    spoken = []

    async def on_text(chunk):
        spoken.append(chunk)

    await slow.process_turn("that works", on_text=on_text)
    assert spoken[0] == "One moment." and "8 AM" in " ".join(spoken)

    quick = _agent(db, contractor, [_FakeStream([("tool", "create_lead_record")], _final("", tool="create_lead_record")),
                                    _FakeStream(["Got it."], _final("Got it."))])
    spoken2 = []

    async def on_text2(chunk):
        spoken2.append(chunk)

    await quick.process_turn("my name is Sam", on_text=on_text2)
    assert spoken2 == ["Got it."]          # silent bookkeeping tools get no filler


@pytest.mark.asyncio
async def test_booking_confirmation_text_is_deferred_on_a_live_call(db, contractor):
    from app.tools import book_appointment as ba
    # only the deferral contract is checked here: the callback list is flushed after the reply
    agent = _agent(db, contractor, [])
    ran = []
    agent._tool_context["after_turn"] = [lambda: _async_append(ran, "sms")]
    await agent.flush_after_turn()
    assert ran == ["sms"] and agent._tool_context["after_turn"] == []


async def _async_append(lst, v):
    lst.append(v)
