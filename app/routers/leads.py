import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.contractor import Contractor
from app.models.lead import Lead
from app.schemas.lead import LeadResponse
from app.utils.auth import get_contractor_from_api_key

router = APIRouter(prefix="/leads", tags=["leads"])
logger = logging.getLogger(__name__)


@router.get("/{lead_id}", response_model=LeadResponse)
async def get_lead(
    lead_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    authed: Contractor = Depends(get_contractor_from_api_key),
):
    """Fetch a single lead by ID. The lead must belong to the authenticated contractor."""
    result = await db.execute(
        select(Lead).where(Lead.id == lead_id, Lead.contractor_id == authed.id)
    )
    lead = result.scalar_one_or_none()
    if lead is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found.")
    return LeadResponse.model_validate(lead)
