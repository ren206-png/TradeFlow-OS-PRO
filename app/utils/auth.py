import logging

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.contractor import Contractor

logger = logging.getLogger(__name__)

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=True)


async def get_contractor_from_api_key(
    api_key: str = Security(_api_key_header),
    db: AsyncSession = Depends(get_db),
) -> Contractor:
    """Resolve an X-API-Key header to the owning Contractor, or raise 403."""
    result = await db.execute(
        select(Contractor).where(Contractor.api_key == api_key, Contractor.is_active.is_(True))
    )
    contractor = result.scalar_one_or_none()
    if contractor is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid or inactive API key.")
    return contractor
