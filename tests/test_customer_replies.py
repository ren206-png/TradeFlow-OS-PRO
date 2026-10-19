"""Free-text customer replies are passed to the owner instead of being dropped."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services import inbound_sms


import uuid


class _C:
    name = "Apex Plumbing"
    owner_phone = "+17805550142"
    calendar_config = {}
    id = uuid.uuid4()


@pytest.mark.asyncio
async def test_reply_is_forwarded_to_the_owner(db):
    inbound_sms._REPLY_FORWARDS.clear()
    send = AsyncMock(return_value={"success": True})
    with patch.object(inbound_sms, "_resolve_tenant_from_to", AsyncMock(return_value=(_C(), "+15550000000"))), \
         patch("app.services.sms_provider.send_sms", send):
        assert await inbound_sms.handle_inbound_sms("+14035550199", "Can you come at 2 instead?", "+15550000000", db) is None
    to, body, _kind = send.await_args.args
    assert to == "+17805550142" and "Can you come at 2 instead?" in body and "+14035550199" in body


@pytest.mark.asyncio
async def test_owner_texting_the_line_and_floods_are_not_forwarded(db):
    inbound_sms._REPLY_FORWARDS.clear()
    send = AsyncMock(return_value={"success": True})
    with patch.object(inbound_sms, "_resolve_tenant_from_to", AsyncMock(return_value=(_C(), "+15550000000"))), \
         patch("app.services.sms_provider.send_sms", send):
        await inbound_sms.handle_inbound_sms("+17805550142", "GO", "+15550000000", db)   # the owner themselves
        assert send.await_count == 0
        for i in range(9):                                                                # a looping auto-responder
            await inbound_sms.handle_inbound_sms("+14035550123", f"auto {i}", "+15550000000", db)
        assert send.await_count == inbound_sms._REPLY_FORWARD_LIMIT


@pytest.mark.asyncio
async def test_stop_keyword_is_not_forwarded(db):
    inbound_sms._REPLY_FORWARDS.clear()
    send = AsyncMock(return_value={"success": True})
    with patch.object(inbound_sms, "_resolve_tenant_from_to", AsyncMock(return_value=(_C(), "+15550000000"))), \
         patch("app.services.sms_provider.send_sms", send):
        await inbound_sms.handle_inbound_sms("+14035550199", "STOP", "+15550000000", db)
    assert send.await_count == 0
