from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class CallSessionResponse(BaseModel):
    id: uuid.UUID
    retell_call_id: str
    contractor_id: uuid.UUID
    lead_id: Optional[uuid.UUID]
    status: str
    started_at: datetime
    ended_at: Optional[datetime]
    duration_seconds: Optional[int]

    model_config = {"from_attributes": True}
