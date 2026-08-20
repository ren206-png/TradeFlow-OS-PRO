"""
Pydantic schemas for the outbound gateway.
All models use ConfigDict(extra="forbid") — unknown fields are rejected.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict


class OutboundRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    recipient_phone: str          # E.164 format, e.g. +12125551234
    channel: Literal["sms", "call", "email"]
    message: Optional[str] = None          # SMS/email body
    template_id: Optional[str] = None
    campaign_id: Optional[str] = None
    call_script_id: Optional[str] = None
    idempotency_key: str                   # required — callers must supply
    metadata: dict = {}


class ConsentRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    recipient_phone: str
    channel: Literal["sms", "call", "email"]
    consent_type: Literal["express", "implied_inbound", "implied_transaction"]
    consent_basis: str                     # human-readable source description
    evidence_call_id: Optional[str] = None
    evidence_form_id: Optional[str] = None
    # null = never expires (express consent); CASL implied = now + 730 days
    expires_at: Optional[datetime] = None


class A2PStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    brand_registration_id: Optional[str] = None
    campaign_id: Optional[str] = None
    status: Literal["unregistered", "pending", "approved", "rejected", "suspended"]
    submitted_at: Optional[datetime] = None
    approved_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None
    updated_at: datetime


class GatewayResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success: bool
    block_reason: Optional[str] = None
    ledger_id: str
