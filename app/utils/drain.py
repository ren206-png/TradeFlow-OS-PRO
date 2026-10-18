"""Tracks live call WebSockets so a deploy can wait for them to finish instead of cutting them off."""
from __future__ import annotations

import threading

_lock = threading.Lock()
_active: set[str] = set()


def call_started(call_id: str) -> None:
    with _lock:
        _active.add(call_id)


def call_finished(call_id: str) -> None:
    with _lock:
        _active.discard(call_id)


def active_count() -> int:
    with _lock:
        return len(_active)
