from .base import FSMAdapter

JOBBER_GRAPHQL_URL = "https://api.getjobber.com/api/graphql"


class JobberAdapter(FSMAdapter):
    """Jobber GraphQL API v2024-01 adapter.
    Creates Request objects (not Jobs — Requests are the intake record in Jobber).
    """

    async def _gql(self, query: str, variables: dict) -> dict:
        resp = await self.client.post(
            JOBBER_GRAPHQL_URL,
            json={"query": query, "variables": variables},
            headers={
                "Authorization": f"Bearer {self.access_token}",
                "X-JOBBER-GRAPHQL-VERSION": "2024-01-15",
                "Content-Type": "application/json",
            },
        )
        resp.raise_for_status()
        return resp.json()

    async def create_lead(self, lead_data: dict) -> dict:
        """Create a Request in Jobber for the new lead."""
        mutation = """
        mutation CreateRequest($input: RequestCreateInput!) {
          requestCreate(input: $input) {
            request {
              id
              status
            }
            userErrors { message }
          }
        }
        """
        variables = {
            "input": {
                "title": f"{lead_data.get('trade', 'Service')} - {lead_data.get('problem_summary', 'New Request')[:80]}",
                "client": {
                    "firstName": ((lead_data.get("caller_name") or "").split() or ["Unknown"])[0],
                    "lastName": " ".join((lead_data.get("caller_name") or "").split()[1:]) or "",
                    "phones": [{"number": lead_data.get("phone", ""), "primary": True}],
                },
                "details": lead_data.get("problem_summary", ""),
            }
        }
        result = await self._gql(mutation, variables)
        return result.get("data", {}).get("requestCreate", {}).get("request", {})

    async def create_job(self, lead_data: dict, appointment_time: str | None) -> dict:
        """For Jobber, a job flows from a Request — we create the Request and let the tech convert it."""
        return await self.create_lead(lead_data)

    async def health_check(self) -> bool:
        query = "{ account { id } }"
        try:
            result = await self._gql(query, {})
            return "account" in result.get("data", {})
        except Exception:
            return False
