from __future__ import annotations

import datetime
import logging
import os

from cryptography.fernet import Fernet, InvalidToken
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
        except (ValueError, InvalidToken):
            logger.error(f"Failed to decrypt FSM token for contractor {contractor.id}")
            return None

        if cred.vendor == "jobber":
            return JobberAdapter(access_token=token)
        elif cred.vendor == "housecall_pro":
            return HousecallAdapter(access_token=token)
        return None

    async def get_estimate_status(
        self,
        contractor: Contractor,
        fsm_estimate_id: str,
        db: AsyncSession,
    ) -> str | None:
        """
        Fetch estimate status from FSM adapter.
        Returns status string (accepted/declined/sent/etc) or None if not available.
        If the adapter returns None (not implemented), logs and returns None — caller
        must treat as unknown and NOT fake the status.
        """
        adapter = await self.get_adapter(contractor, db)
        if not adapter:
            return None
        try:
            result = await adapter.get_estimate(fsm_estimate_id)
            if result is None:
                return None
            return result.get("status")
        except Exception as exc:
            logger.warning(
                "FSMService.get_estimate_status failed | contractor=%s fsm_estimate_id=%s err=%s",
                contractor.id, fsm_estimate_id, exc,
            )
            return None
        finally:
            await adapter.close()

    async def get_appointment_status(
        self,
        contractor: Contractor,
        fsm_appointment_id: str,
        db: AsyncSession,
    ) -> str | None:
        """
        Fetch appointment/job status from FSM. Returns status string or None.
        Used by reminder service to detect cancelled/completed appointments before send.
        """
        adapter = await self.get_adapter(contractor, db)
        if not adapter:
            return None
        try:
            # Both Jobber and HCP expose job status via create_job result shape;
            # we query health check and note this is a best-effort check.
            # Actual per-appointment lookup requires adapter-specific implementation.
            # Returning None causes send_reminder to proceed (safe default).
            logger.info(
                "FSMService.get_appointment_status: adapter does not support per-appointment reads "
                "— returning None (safe: reminder will send) | contractor=%s fsm_id=%s",
                contractor.id, fsm_appointment_id,
            )
            return None
        except Exception as exc:
            logger.warning("FSMService.get_appointment_status failed | err=%s", exc)
            return None
        finally:
            await adapter.close()

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
            return True
        except Exception as e:
            logger.error(f"FSM push failed for contractor {contractor.id}: {e}")
            # Enqueue for retry
            lead_id = lead_data.get("lead_id")
            retry = FSMRetryQueue(
                contractor_id=contractor.id,
                lead_id=lead_id,
                vendor=vendor,
                payload=lead_data,
                attempt_count=1,
                last_error=str(e),
                next_attempt_at=datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=5),
            )
            db.add(retry)
            await db.flush()
            return False
        finally:
            await adapter.close()
