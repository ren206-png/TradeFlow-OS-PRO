from __future__ import annotations

import datetime
import logging
import os

from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.contractor import Contractor
from app.models.fsm_credential import FSMCredential
from app.models.fsm_retry_queue import FSMRetryQueue
from .jobber import JobberAdapter
from .housecall import HousecallAdapter

logger = logging.getLogger(__name__)


def _fernet() -> Fernet:
    key = os.environ.get("ENCRYPTION_KEY", "")
    if not key:
        raise ValueError("ENCRYPTION_KEY env var not set")
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_token(token: str) -> str:
    return _fernet().encrypt(token.encode()).decode()


def decrypt_token(enc: str) -> str:
    return _fernet().decrypt(enc.encode()).decode()


class FSMService:
    async def get_adapter(self, contractor: Contractor, db: AsyncSession):
        """Get the FSM adapter for this contractor, or None if not configured."""
        result = await db.execute(
            select(FSMCredential).where(FSMCredential.contractor_id == contractor.id)
        )
        cred = result.scalar_one_or_none()
        if not cred:
            return None
        try:
            token = decrypt_token(cred.access_token_enc)
        except Exception:
            logger.error(f"Failed to decrypt FSM token for contractor {contractor.id}")
            return None

        if cred.vendor == "jobber":
            return JobberAdapter(access_token=token)
        elif cred.vendor == "housecall_pro":
            return HousecallAdapter(access_token=token)
        return None

    async def push_lead(
        self,
        contractor: Contractor,
        lead_data: dict,
        appointment_time: str | None,
        db: AsyncSession,
    ) -> bool:
        """Push lead to FSM. Enqueues for retry on failure. Returns True on success."""
        # Fetch credential first so we have the vendor for retry queue
        result = await db.execute(
            select(FSMCredential).where(FSMCredential.contractor_id == contractor.id)
        )
        cred = result.scalar_one_or_none()
        if not cred:
            return False

        vendor = cred.vendor
        adapter = await self.get_adapter(contractor, db)
        if not adapter:
            return False

        try:
            if appointment_time:
                await adapter.create_job(lead_data, appointment_time)
            else:
                await adapter.create_lead(lead_data)
            await adapter.close()
            return True
        except Exception as e:
            logger.error(f"FSM push failed for contractor {contractor.id}: {e}")
            await adapter.close()
            # Enqueue for retry
            lead_id = lead_data.get("lead_id")
            retry = FSMRetryQueue(
                contractor_id=contractor.id,
                lead_id=lead_id,
                vendor=vendor,
                payload=lead_data,
                attempt_count=1,
                last_error=str(e),
                next_attempt_at=datetime.datetime.utcnow() + datetime.timedelta(minutes=5),
            )
            db.add(retry)
            await db.flush()
            return False
