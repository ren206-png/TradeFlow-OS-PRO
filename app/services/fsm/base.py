from abc import ABC, abstractmethod

import httpx


class FSMAdapter(ABC):
    """Abstract base for FSM vendor adapters."""

    def __init__(self, access_token: str):
        self.access_token = access_token
        self.client = httpx.AsyncClient(timeout=15.0)

    @abstractmethod
    async def create_lead(self, lead_data: dict) -> dict:
        """Push a new lead/request to the FSM. Returns FSM record id."""
        ...

    @abstractmethod
    async def create_job(self, lead_data: dict, appointment_time: str | None) -> dict:
        """Create a job/appointment in the FSM. Returns FSM record id."""
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """Verify the token works. Returns True if authenticated."""
        ...

    async def close(self):
        await self.client.aclose()
