from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class ContractorCreate(BaseModel):
    name: str
    agent_name: str
    phone_number: str
    api_key: str
    trades: List[str] = Field(default_factory=list)
    service_areas: List[str] = Field(default_factory=list)
    timezone: str = "America/New_York"
    diagnostic_fee: Optional[float] = None
    free_estimate: bool = False
    calendar_provider: str = "manual"
    calendar_config: dict = Field(default_factory=dict)
    sms_enabled: bool = True
    review_link: Optional[str] = None


class ContractorUpdate(BaseModel):
    name: Optional[str] = None
    agent_name: Optional[str] = None
    phone_number: Optional[str] = None
    trades: Optional[List[str]] = None
    service_areas: Optional[List[str]] = None
    timezone: Optional[str] = None
    diagnostic_fee: Optional[float] = None
    free_estimate: Optional[bool] = None
    calendar_provider: Optional[str] = None
    calendar_config: Optional[dict] = None
    sms_enabled: Optional[bool] = None
    review_link: Optional[str] = None
    is_active: Optional[bool] = None


class ContractorResponse(BaseModel):
    id: uuid.UUID
    name: str
    agent_name: str
    phone_number: str
    trades: List[str]
    service_areas: List[str]
    timezone: str
    diagnostic_fee: Optional[float]
    free_estimate: bool
    calendar_provider: str
    sms_enabled: bool
    review_link: Optional[str]
    retell_agent_id: Optional[str]
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
