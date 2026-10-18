"""Graceful deploys: SIGTERM waits for live calls instead of closing their WebSockets."""
from __future__ import annotations

import signal
import time

import uvicorn

from app import server
from app.utils import drain


def _server():
    return server.DrainingServer(uvicorn.Config("app.main:app"))


def test_drain_pauses_scheduled_jobs_so_they_do_not_run_twice(monkeypatch):
    from unittest.mock import MagicMock
    sched = MagicMock(running=True)
    monkeypatch.setattr("app.services.scheduler.get_scheduler", lambda: sched)
    drain.call_started("call-y")
    try:
        s = _server()
        s.handle_exit(signal.SIGTERM, None)
        sched.pause.assert_called_once()
    finally:
        drain.call_finished("call-y")


def test_sigterm_with_no_calls_exits_immediately():
    s = _server()
    s.handle_exit(signal.SIGTERM, None)
    assert s.should_exit is True


def test_sigterm_waits_for_live_call_then_exits(monkeypatch):
    drain.call_started("call-x")
    s = _server()
    s.handle_exit(signal.SIGTERM, None)
    s.handle_exit(signal.SIGTERM, None)  # a second SIGTERM must not cut the drain short
    time.sleep(1.5)
    assert s.should_exit is False
    drain.call_finished("call-x")
    for _ in range(40):
        if s.should_exit:
            break
        time.sleep(0.1)
    assert s.should_exit is True


def test_drain_gives_up_after_the_deadline(monkeypatch):
    monkeypatch.setattr(server, "DRAIN_MAX_SECONDS", 1)
    drain.call_started("call-stuck")
    try:
        s = _server()
        s.handle_exit(signal.SIGTERM, None)
        time.sleep(2.5)
        assert s.should_exit is True
    finally:
        drain.call_finished("call-stuck")
