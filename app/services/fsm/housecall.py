import uuid

from .base import FSMAdapter

HCP_BASE_URL = "https://api.housecallpro.com"


class HousecallAdapter(FSMAdapter):
    """Housecall Pro REST API v1 adapter.
    Creates Job objects directly.
    """

    async def _post(self, path: str, data: dict, idempotency_key: str | None = None) -> dict:
        headers = {
            "Authorization": f"Token token={self.access_token}",
            "Content-Type": "application/json",
        }
        if idempotency_key:
            headers["X-Idempotency-Key"] = idempotency_key
        resp = await self.client.post(f"{HCP_BASE_URL}{path}", json=data, headers=headers)
        resp.raise_for_status()
        return resp.json()

    async def create_lead(self, lead_data: dict) -> dict:
        """Create a job in HCP for the lead (HCP uses jobs as the intake record)."""
        idempotency_key = str(uuid.uuid4())
        payload = {
            "job": {
                "customer": {
                    "first_name": (lead_data.get("caller_name") or "Unknown").split()[0],
                    "last_name": " ".join((lead_data.get("caller_name") or "").split()[1:]),
                    "mobile_number": lead_data.get("phone", ""),
                    "email": lead_data.get("email", ""),
                },
                "address": {
                    "street": lead_data.get("service_address", ""),
                    "city": lead_data.get("city", ""),
                    "state": lead_data.get("province_state", ""),
                    "zip": lead_data.get("postal_zip", ""),
                },
                "description": lead_data.get("problem_summary", ""),
                "tags": [lead_data.get("trade", "service")],
            }
        }
        result = await self._post("/v1/jobs", payload, idempotency_key=idempotency_key)
        return result

    async def create_job(self, lead_data: dict, appointment_time: str | None) -> dict:
        payload_extra = {}
        if appointment_time:
            payload_extra["scheduled_start"] = appointment_time
        return await self.create_lead({**lead_data, **payload_extra})

    async def health_check(self) -> bool:
        try:
            headers = {"Authorization": f"Token token={self.access_token}"}
            resp = await self.client.get(f"{HCP_BASE_URL}/v1/company", headers=headers)
            return resp.status_code == 200
        except Exception:
            return False
