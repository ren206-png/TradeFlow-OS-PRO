from __future__ import annotations

import hashlib
import secrets
from typing import Optional

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError

_ph = PasswordHasher(time_cost=2, memory_cost=65536, parallelism=2, hash_len=32, salt_len=16)

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
    """Hash using argon2id. New hashes start with '$argon2id$'."""
    return _ph.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    """
    Verify a password against its stored hash.
    Supports both argon2id (new) and legacy PBKDF2-SHA256 (old format: '<salt>$<hex>').
    Existing users are migrated transparently on next login — the caller must persist
    the new hash if this function returns (True, new_hash).
    Returns True/False; does NOT raise.
    """
    if not hashed:
        return False

    # --- argon2id path ---
    if hashed.startswith("$argon2"):
        try:
            return _ph.verify(hashed, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False

    # --- legacy PBKDF2 path (backward compat) ---
    try:
        salt, key_hex = hashed.split("$")
        key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260000)
        return secrets.compare_digest(key.hex(), key_hex)
    except Exception:
        return False


def needs_rehash(hashed: str) -> bool:
    """True if the stored hash should be upgraded to the current argon2id parameters."""
    if not hashed or not hashed.startswith("$argon2"):
        return True
    return _ph.check_needs_rehash(hashed)
