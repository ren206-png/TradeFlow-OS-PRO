"""
Simple in-memory rate limiter for auth endpoints.

Uses a sliding-window counter keyed by (IP, action).
Intentionally lightweight — no Redis needed for this scale.
On multi-replica deploys, replace with a Redis-backed counter.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from threading import Lock
from typing import Tuple

_lock = Lock()
# {key: deque of timestamps}
_windows: dict[str, deque] = defaultdict(deque)


def _client_ip(request) -> str:
    """Best-effort client IP extraction, respecting X-Forwarded-For from Railway's proxy."""
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def check_rate_limit(
    request,
    action: str,
    max_requests: int = 10,
    window_seconds: int = 600,
) -> Tuple[bool, int]:
    """
    Check if the request is within rate limits.

    Returns (allowed: bool, retry_after_seconds: int).
    retry_after_seconds is 0 when allowed=True.
    """
    ip = _client_ip(request)
    key = f"{ip}:{action}"
    now = time.monotonic()
    cutoff = now - window_seconds

    with _lock:
        window = _windows[key]
        # Evict expired entries
        while window and window[0] < cutoff:
            window.popleft()

        if len(window) >= max_requests:
            oldest = window[0]
            retry_after = int(window_seconds - (now - oldest)) + 1
            return False, retry_after

        window.append(now)
        return True, 0
