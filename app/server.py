"""
Production entrypoint (start.sh runs `python -m app.server`).

A plain `uvicorn` process closes every live WebSocket the moment a deploy sends SIGTERM, which hangs up
callers mid-call (Retell reports error_llm_websocket_runtime). This server, on SIGTERM, keeps running until
the live calls end (or DRAIN_MAX_SECONDS), then shuts down normally. Railway must allow at least that long
between SIGTERM and SIGKILL: set RAILWAY_DEPLOYMENT_DRAINING_SECONDS above DRAIN_MAX_SECONDS.
"""
from __future__ import annotations

import logging
import os
import signal
import threading
import time

import uvicorn

from app.utils import drain

logger = logging.getLogger("uvicorn.error")

DRAIN_MAX_SECONDS = int(os.environ.get("DRAIN_MAX_SECONDS", "280"))


class DrainingServer(uvicorn.Server):
    _draining = False

    def handle_exit(self, sig, frame) -> None:
        if sig == signal.SIGTERM and not self._draining and drain.active_count() > 0:
            self._draining = True
            self._hand_background_jobs_to_new_version()
            logger.info("SIGTERM with %d live call(s): draining for up to %ds", drain.active_count(), DRAIN_MAX_SECONDS)
            threading.Thread(target=self._drain_then_exit, args=(sig, frame), daemon=True).start()
            return
        if self._draining and sig == signal.SIGTERM:
            return  # a repeated SIGTERM must not cut the drain short
        super().handle_exit(sig, frame)

    @staticmethod
    def _hand_background_jobs_to_new_version() -> None:
        """The new version is already serving; stop this one's scheduled jobs so none run twice during the overlap."""
        try:
            from app.services.scheduler import get_scheduler
            scheduler = get_scheduler()
            if scheduler.running:
                scheduler.pause()
                logger.info("Scheduler paused: the new version owns scheduled jobs")
        except Exception as exc:  # never let this block the drain
            logger.warning("Could not pause scheduler during drain: %s", exc)

    def _drain_then_exit(self, sig, frame) -> None:
        deadline = time.monotonic() + DRAIN_MAX_SECONDS
        while drain.active_count() > 0 and time.monotonic() < deadline:
            time.sleep(1)
        logger.info("Drain finished with %d call(s) still live; shutting down", drain.active_count())
        uvicorn.Server.handle_exit(self, sig, frame)


if __name__ == "__main__":
    config = uvicorn.Config("app.main:app", host="0.0.0.0", port=int(os.environ.get("PORT", "8000")),
                            workers=1, log_level="info")
    DrainingServer(config).run()
