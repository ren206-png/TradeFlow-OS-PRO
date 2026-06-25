from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class LeadResponse(BaseModel):
    id: uuid.UUID
    contractor_id: uuid.UUID
    call_id: str
    caller_name: Optional[str]
    phone: Optional[str]
    email: Optional[str]
    service_address: Optional[str]
    city: Optional[str]
    province_state: Optional[str]
    postal_zip: Optional[str]
    property_type: Optional[str]
    business_name: Optional[str]
    trade: Optional[str]
    service_category: Optional[str]
    problem_summary: Optional[str]
    emergency_level: Optional[str]
    life_safety_risk: bool
    service_area_status: str
    appointment_status: str
    appointment_time: Optional[datetime]
    sms_confirmation_sent: bool
    human_transfer_required: bool
    transfer_reason: Optional[str]
    emergency_score: Optional[int]
    revenue_score: Optional[int]
    close_probability: Optional[int]
    priority_level: Optional[str]
    customer_sentiment: Optional[str]
    notes: Optional[str]
    call_direction: str
    lead_source: str
    recording_url: Optional[str]
    transcript_url: Optional[str]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class LeadListResponse(BaseModel):
    leads: List[LeadResponse]
    total: int
    page: int
    page_size: int
