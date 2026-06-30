from __future__ import annotations
"""
In-memory pub/sub event bus for broadcasting call state to dashboard WebSocket clients.
Fine for single-worker deployment; replace with Redis pub/sub for horizontal scaling.
"""

import logging
from typing import Set

from fastapi import WebSocket

logger = logging.getLogger(__name__)

_dashboard_clients: Set[WebSocket] = set()


async def register_dashboard_client(ws: WebSocket) -> None:
    _dashboard_clients.add(ws)
    logger.info("Dashboard client registered | total=%d", len(_dashboard_clients))


async def unregister_dashboard_client(ws: WebSocket) -> None:
    _dashboard_clients.discard(ws)
    logger.info("Dashboard client unregistered | total=%d", len(_dashboard_clients))


async def broadcast_call_event(event: dict) -> None:
    """Send a JSON event to all connected dashboard clients; prune dead connections."""
    dead: Set[WebSocket] = set()
    for ws in list(_dashboard_clients):
        try:
            await ws.send_json(event)
        except Exception:
            dead.add(ws)
    for ws in dead:
        _dashboard_clients.discard(ws)
        logger.info("Pruned dead dashboard client | remaining=%d", len(_dashboard_clients))
