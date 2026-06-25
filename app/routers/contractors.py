from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.contractor import Contractor
from app.models.lead import Lead
from app.schemas.contractor import ContractorCreate, ContractorResponse, ContractorUpdate
from app.schemas.lead import LeadListResponse, LeadResponse
from app.services.retell_client import RetellClient
from app.utils.auth import get_contractor_from_api_key

router = APIRouter(prefix="/contractors", tags=["contractors"])
logger = logging.getLogger(__name__)


@router.post("", response_model=ContractorResponse, status_code=status.HTTP_201_CREATED)
async def create_contractor(
    body: ContractorCreate,
    db: AsyncSession = Depends(get_db),
    _: Contractor = Depends(get_contractor_from_api_key),  # admin must auth with any valid key
):
    """Create a new contractor. Requires a valid X-API-Key from an existing contractor."""
    contractor = Contractor(**body.model_dump())
    db.add(contractor)
    await db.flush()
    return ContractorResponse.model_validate(contractor)


@router.get("/{contractor_id}", response_model=ContractorResponse)
async def get_contractor(
    contractor_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    authed: Contractor = Depends(get_contractor_from_api_key),
):
    _assert_owns(authed, contractor_id)
    contractor = await _get_or_404(contractor_id, db)
    return ContractorResponse.model_validate(contractor)


@router.put("/{contractor_id}", response_model=ContractorResponse)
async def update_contractor(
    contractor_id: uuid.UUID,
    body: ContractorUpdate,
    db: AsyncSession = Depends(get_db),
    authed: Contractor = Depends(get_contractor_from_api_key),
):
    _assert_owns(authed, contractor_id)
    contractor = await _get_or_404(contractor_id, db)

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(contractor, field, value)
    contractor.updated_at = datetime.now(tz=timezone.utc)

    await db.flush()
    return ContractorResponse.model_validate(contractor)


@router.get("/{contractor_id}/leads", response_model=LeadListResponse)
async def list_leads(
    contractor_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    authed: Contractor = Depends(get_contractor_from_api_key),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    priority_level: Optional[str] = None,
    trade: Optional[str] = None,
    appointment_status: Optional[str] = None,
):
    _assert_owns(authed, contractor_id)

    query = select(Lead).where(Lead.contractor_id == contractor_id)
    if date_from:
        query = query.where(Lead.created_at >= date_from)
    if date_to:
        query = query.where(Lead.created_at <= date_to)
    if priority_level:
        query = query.where(Lead.priority_level == priority_level)
    if trade:
        query = query.where(Lead.trade == trade)
    if appointment_status:
        query = query.where(Lead.appointment_status == appointment_status)

    count_result = await db.execute(select(func.count()).select_from(query.subquery()))
    total = count_result.scalar_one()

    query = query.order_by(Lead.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    leads = result.scalars().all()

    return LeadListResponse(
        leads=[LeadResponse.model_validate(l) for l in leads],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{contractor_id}/leads/{lead_id}", response_model=LeadResponse)
async def get_lead(
    contractor_id: uuid.UUID,
    lead_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    authed: Contractor = Depends(get_contractor_from_api_key),
):
    _assert_owns(authed, contractor_id)
    result = await db.execute(
        select(Lead).where(Lead.id == lead_id, Lead.contractor_id == contractor_id)
    )
    lead = result.scalar_one_or_none()
    if lead is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found.")
    return LeadResponse.model_validate(lead)


# ---------------------------------------------------------------------------
# Retell agent provisioning
# ---------------------------------------------------------------------------

@router.post("/{contractor_id}/provision-retell-agent", response_model=ContractorResponse)
async def provision_retell_agent(
    contractor_id: uuid.UUID,
    public_base_url: str = Query(..., description="Your public server URL, e.g. https://api.myapp.com"),
    voice_id: str = Query("11labs-Adrian", description="Retell voice ID to use for this contractor"),
    db: AsyncSession = Depends(get_db),
    authed: Contractor = Depends(get_contractor_from_api_key),
):
    """
    Create (or replace) a Retell Custom LLM agent for this contractor.

    This wires the agent's llm_websocket_url to point at this server's
    WebSocket endpoint so Retell calls Claude for every turn.

    Call once during contractor onboarding. Stores the returned agent_id
    on the Contractor record so outbound calls can reference it.
    """
    _assert_owns(authed, contractor_id)
    contractor = await _get_or_404(contractor_id, db)

    # Retell substitutes {call_id} at connection time
    websocket_url = f"{public_base_url.rstrip('/')}/llm-websocket/{{call_id}}"

    retell_client = RetellClient()

    # Custom LLM agents require response_engine.type = "custom_llm" and
    # response_engine.llm_websocket_url pointing at our WebSocket endpoint.
    agent_config = {
        "agent_name": f"{contractor.agent_name} — {contractor.name}",
        "voice_id": voice_id,
        "response_engine": {
            "type": "custom_llm",
            "llm_websocket_url": websocket_url,
        },
        "language": "en-US",
        "enable_backchannel": True,
        "responsiveness": 1.0,
        "interruption_sensitivity": 0.8,
    }

    if contractor.retell_agent_id:
        result = await retell_client.update_agent(contractor.retell_agent_id, agent_config)
    else:
        result = await retell_client.create_agent(agent_config)

    contractor.retell_agent_id = result.get("agent_id") or result.get("id")
    contractor.updated_at = datetime.now(tz=timezone.utc)
    await db.flush()

    logger.info(
        "Retell agent provisioned | contractor=%s agent_id=%s ws_url=%s",
        contractor.name, contractor.retell_agent_id, websocket_url,
    )
    return ContractorResponse.model_validate(contractor)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _assert_owns(authed: Contractor, contractor_id: uuid.UUID) -> None:
    if authed.id != contractor_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied.")


async def _get_or_404(contractor_id: uuid.UUID, db: AsyncSession) -> Contractor:
    result = await db.execute(select(Contractor).where(Contractor.id == contractor_id))
    contractor = result.scalar_one_or_none()
    if contractor is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contractor not found.")
    return contractor
