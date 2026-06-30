from __future__ import annotations

import hashlib
import secrets
from typing import Optional

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def get_contractor_from_api_key(
    api_key: Optional[str] = Security(_api_key_header),
    db: AsyncSession = Depends(get_db),
):
    """FastAPI dependency: resolve X-API-Key header to a Contractor."""
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key.",
        )
    from app.models.contractor import Contractor  # local import to avoid circular
    result = await db.execute(
        select(Contractor).where(
            Contractor.api_key == api_key,
            Contractor.is_active.is_(True),
        )
    )
    contractor = result.scalar_one_or_none()
    if contractor is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key.",
        )
    return contractor


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260000)
    return f"{salt}${key.hex()}"


def verify_password(password: str, hashed: str) -> bool:
    try:
        salt, key_hex = hashed.split("$")
        key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260000)
        return secrets.compare_digest(key.hex(), key_hex)
    except Exception:
        return False
