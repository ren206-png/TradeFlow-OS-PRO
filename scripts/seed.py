"""
Seed script: creates a demo TradeFlow Pro contractor in the database.

Usage:
    python scripts/seed.py

Set DATABASE_URL in environment or .env; falls back to SQLite for local testing.
"""
from __future__ import annotations

import asyncio
import os
import secrets
import sys
from pathlib import Path
from typing import Optional

# Allow running from repo root without installing the package
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# ---------------------------------------------------------------------------
# Database URL — prefer env var, fall back to local SQLite
# ---------------------------------------------------------------------------
_DEFAULT_SQLITE = "sqlite+aiosqlite:///./tradeflow_local.db"
DATABASE_URL: str = os.environ.get("DATABASE_URL", _DEFAULT_SQLITE)

# asyncpg DSNs starting with "postgres://" need the "+asyncpg" driver prefix
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
elif DATABASE_URL.startswith("postgresql://") and "+asyncpg" not in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

# ---------------------------------------------------------------------------
# SQLAlchemy setup (standalone — does NOT import app.database so the engine
# can use the overridden DATABASE_URL rather than the one baked into settings)
# ---------------------------------------------------------------------------
engine = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
SessionFactory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

# Import models *after* engine is created so Base.metadata is populated
from app.models.contractor import Contractor  # noqa: E402
from app.database import Base  # noqa: E402  (pulls in all model metadata)

# ---------------------------------------------------------------------------
# Demo contractor data
# ---------------------------------------------------------------------------
DEMO: dict = {
    "name": "TradeFlow Pro Demo",
    "agent_name": "Alex",
    "phone_number": "+15550001234",  # placeholder — update after seeding
    "trades": [
        "plumbing",
        "hvac",
        "roofing",
        "electrical",
        "garage_door",
        "locksmith",
        "towing",
    ],
    "service_areas": [
        "T2N", "T2P", "T2R", "T2S", "T2T", "T2V", "T2W", "T2X", "T2Y", "T2Z",
        "T3A", "T3B", "T3C", "T3E", "T3G", "T3H", "T3J", "T3K", "T3L", "T3M",
        "T3N", "T3P", "T3R", "Calgary",
    ],
    "timezone": "America/Edmonton",
    "diagnostic_fee": 99.0,
    "free_estimate": False,
    "calendar_provider": "google",
    "calendar_config": {"transfer_number": "+15550009999"},
    "sms_enabled": True,
    "review_link": "https://g.page/tradeflow-pro",
    "retell_agent_id": "agent_d0c20e096579a7a33a681c3849",
    "is_active": True,
}


async def seed() -> None:
    # Ensure tables exist (safe no-op if already created by Alembic)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with SessionFactory() as session:
        # Idempotency check — phone_number is the unique natural key
        result = await session.execute(
            select(Contractor).where(Contractor.phone_number == DEMO["phone_number"])
        )
        existing: Optional[Contractor] = result.scalar_one_or_none()

        if existing is not None:
            print("Demo contractor already exists — skipping insert.")
            print(f"  Contractor ID : {existing.id}")
            print(f"  API Key       : {existing.api_key}")
            return

        contractor = Contractor(
            api_key=secrets.token_hex(32),
            **DEMO,
        )
        session.add(contractor)
        await session.commit()
        await session.refresh(contractor)

    print("Demo contractor created successfully.")
    print(f"  Contractor ID : {contractor.id}")
    print(f"  API Key       : {contractor.api_key}")
    print()
    print("Update phone_number in the DB (or re-seed) when you have a real Twilio number.")


if __name__ == "__main__":
    asyncio.run(seed())
