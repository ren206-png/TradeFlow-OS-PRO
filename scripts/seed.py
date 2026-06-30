"""
Seed script: creates demo TradeFlow Pro contractors and leads in the database.

Usage:
    python scripts/seed.py

Set DATABASE_URL in environment or .env; falls back to SQLite for local testing.
"""
from __future__ import annotations

import asyncio
import os
import secrets
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

# Allow running from repo root without installing the package
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select
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
from app.models.lead import Lead  # noqa: E402
from app.database import Base  # noqa: E402  (pulls in all model metadata)

# ---------------------------------------------------------------------------
# Demo contractor data
# ---------------------------------------------------------------------------
DEMO_CONTRACTORS = [
    {
        "name": "Fix-It Fast Plumbing",
        "agent_name": "Jordan",
        "phone_number": "+14035550101",
        "trades": ["plumbing"],
        "service_areas": ["T2N", "T2P", "T2R", "Calgary"],
        "diagnostic_fee": 89.0,
        "free_estimate": False,
        "timezone": "America/Edmonton",
        "calendar_provider": "manual",
        "sms_enabled": True,
        "plan": "starter",
        "is_active": True,
        "calls_this_month": 0,
        "sms_this_month": 0,
    },
    {
        "name": "CoolAir HVAC Services",
        "agent_name": "Alex",
        "phone_number": "+14035550202",
        "trades": ["hvac", "heating", "cooling"],
        "service_areas": ["T2E", "T2G", "T2H", "Calgary"],
        "diagnostic_fee": 119.0,
        "free_estimate": True,
        "timezone": "America/Edmonton",
        "calendar_provider": "manual",
        "sms_enabled": True,
        "plan": "starter",
        "is_active": True,
        "calls_this_month": 0,
        "sms_this_month": 0,
    },
    {
        "name": "SparkRight Electrical",
        "agent_name": "Casey",
        "phone_number": "+14035550303",
        "trades": ["electrical"],
        "service_areas": ["T3A", "T3B", "T3C", "Calgary"],
        "diagnostic_fee": 99.0,
        "free_estimate": False,
        "timezone": "America/Edmonton",
        "calendar_provider": "manual",
        "sms_enabled": True,
        "plan": "starter",
        "is_active": True,
        "calls_this_month": 0,
        "sms_this_month": 0,
    },
]

# ---------------------------------------------------------------------------
# Demo lead data per contractor (keyed by contractor name)
# ---------------------------------------------------------------------------
DEMO_LEADS: dict = {
    "Fix-It Fast Plumbing": [
        {
            "caller_name": "Sarah Mitchell",
            "phone": "+14035551001",
            "trade": "plumbing",
            "problem_summary": "Burst pipe under kitchen sink, water flooding the cabinet.",
            "emergency_level": "emergency",
            "appointment_status": "booked",
            "service_area_status": "inside",
            "call_direction": "inbound",
            "emergency_score": 9,
            "revenue_score": 8,
            "hours_ago": 2,
            "ai_summary": "Emergency burst pipe under kitchen sink requiring immediate repair.",
            "sentiment": "negative",
        },
        {
            "caller_name": "Tom Kovacs",
            "phone": "+14035551002",
            "trade": "plumbing",
            "problem_summary": "Toilet running constantly and leaking around the base.",
            "emergency_level": "same_day",
            "appointment_status": "booked",
            "service_area_status": "inside",
            "call_direction": "inbound",
            "emergency_score": 5,
            "revenue_score": 6,
            "hours_ago": 8,
            "ai_summary": "Running toilet with base leak, same-day repair scheduled.",
            "sentiment": "neutral",
        },
        {
            "caller_name": "Linda Patel",
            "phone": "+14035551003",
            "trade": "plumbing",
            "problem_summary": "Low water pressure throughout the house for the past week.",
            "emergency_level": "flexible",
            "appointment_status": "pending",
            "service_area_status": "inside",
            "call_direction": "inbound",
            "emergency_score": 3,
            "revenue_score": 5,
            "hours_ago": 24,
            "ai_summary": "Persistent low water pressure issue, flexible scheduling requested.",
            "sentiment": "neutral",
        },
        {
            "caller_name": "Marcus Webb",
            "phone": "+14035551004",
            "trade": "plumbing",
            "problem_summary": "Drain completely blocked, sewage backing up into basement.",
            "emergency_level": "emergency",
            "appointment_status": "booked",
            "service_area_status": "inside",
            "call_direction": "inbound",
            "emergency_score": 10,
            "revenue_score": 9,
            "hours_ago": 1,
            "ai_summary": "Sewage backup emergency in basement, priority dispatch required.",
            "sentiment": "negative",
        },
    ],
    "CoolAir HVAC Services": [
        {
            "caller_name": "Jennifer Cho",
            "phone": "+14035552001",
            "trade": "hvac",
            "problem_summary": "AC not cooling at all, house sitting at 85F in the heat.",
            "emergency_level": "same_day",
            "appointment_status": "booked",
            "service_area_status": "inside",
            "call_direction": "inbound",
            "emergency_score": 7,
            "revenue_score": 8,
            "hours_ago": 3,
            "ai_summary": "AC failure in summer heat, same-day service appointment booked.",
            "sentiment": "negative",
        },
        {
            "caller_name": "Brian Osei",
            "phone": "+14035552002",
            "trade": "heating",
            "problem_summary": "Furnace making loud banging noise on startup every morning.",
            "emergency_level": "flexible",
            "appointment_status": "pending",
            "service_area_status": "inside",
            "call_direction": "inbound",
            "emergency_score": 4,
            "revenue_score": 7,
            "hours_ago": 12,
            "ai_summary": "Furnace startup noise likely due to delayed ignition, inspection recommended.",
            "sentiment": "neutral",
        },
        {
            "caller_name": "Diane Marchetti",
            "phone": "+14035552003",
            "trade": "cooling",
            "problem_summary": "AC unit leaking water inside, dripping onto ceiling tiles.",
            "emergency_level": "same_day",
            "appointment_status": "booked",
            "service_area_status": "inside",
            "call_direction": "inbound",
            "emergency_score": 6,
            "revenue_score": 7,
            "hours_ago": 6,
            "ai_summary": "AC condensate leak causing ceiling damage, same-day repair booked.",
            "sentiment": "negative",
        },
        {
            "caller_name": "Kevin Huang",
            "phone": "+14035552004",
            "trade": "hvac",
            "problem_summary": "Annual furnace tune-up before winter season.",
            "emergency_level": "flexible",
            "appointment_status": "booked",
            "service_area_status": "inside",
            "call_direction": "inbound",
            "emergency_score": 1,
            "revenue_score": 5,
            "hours_ago": 36,
            "ai_summary": "Routine annual furnace maintenance scheduled for pre-winter tune-up.",
            "sentiment": "positive",
        },
        {
            "caller_name": "Rachel Simmons",
            "phone": "+14035552005",
            "trade": "hvac",
            "problem_summary": "Thermostat not responding, temperature keeps fluctuating.",
            "emergency_level": "same_day",
            "appointment_status": "not_booked",
            "service_area_status": "inside",
            "call_direction": "inbound",
            "emergency_score": 5,
            "revenue_score": 4,
            "hours_ago": 18,
            "ai_summary": "Unresponsive thermostat causing temperature swings, diagnostics needed.",
            "sentiment": "neutral",
        },
    ],
    "SparkRight Electrical": [
        {
            "caller_name": "David Torres",
            "phone": "+14035553001",
            "trade": "electrical",
            "problem_summary": "Breaker tripping repeatedly in the kitchen, no power to appliances.",
            "emergency_level": "same_day",
            "appointment_status": "booked",
            "service_area_status": "inside",
            "call_direction": "inbound",
            "emergency_score": 7,
            "revenue_score": 7,
            "hours_ago": 4,
            "ai_summary": "Recurring kitchen breaker trips, possible overload or short circuit.",
            "sentiment": "negative",
        },
        {
            "caller_name": "Natalie Burns",
            "phone": "+14035553002",
            "trade": "electrical",
            "problem_summary": "Outdoor outlets stopped working after last night's storm.",
            "emergency_level": "flexible",
            "appointment_status": "pending",
            "service_area_status": "inside",
            "call_direction": "inbound",
            "emergency_score": 3,
            "revenue_score": 4,
            "hours_ago": 14,
            "ai_summary": "Storm-damaged outdoor outlets, likely tripped GFCI or wiring issue.",
            "sentiment": "neutral",
        },
        {
            "caller_name": "Ahmed Khalil",
            "phone": "+14035553003",
            "trade": "electrical",
            "problem_summary": "Burning smell coming from electrical panel, lights flickering.",
            "emergency_level": "emergency",
            "appointment_status": "booked",
            "service_area_status": "inside",
            "call_direction": "inbound",
            "emergency_score": 10,
            "revenue_score": 9,
            "hours_ago": 1,
            "ai_summary": "Critical electrical emergency with burning smell and panel issues, immediate dispatch.",
            "sentiment": "negative",
        },
        {
            "caller_name": "Priya Nair",
            "phone": "+14035553004",
            "trade": "electrical",
            "problem_summary": "Need 240V outlet installed for new EV charger in garage.",
            "emergency_level": "flexible",
            "appointment_status": "booked",
            "service_area_status": "inside",
            "call_direction": "inbound",
            "emergency_score": 1,
            "revenue_score": 8,
            "hours_ago": 30,
            "ai_summary": "EV charger installation requiring new 240V dedicated circuit in garage.",
            "sentiment": "positive",
        },
    ],
}


async def seed() -> None:
    # Ensure tables exist (safe no-op if already created by Alembic)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    seeded_contractors = []

    async with SessionFactory() as session:
        for data in DEMO_CONTRACTORS:
            name = data["name"]

            # Idempotency check by name
            result = await session.execute(
                select(Contractor).where(Contractor.name == name)
            )
            existing: Optional[Contractor] = result.scalar_one_or_none()

            if existing is not None:
                print(f"  [skip] {name} already exists.")
                seeded_contractors.append(existing)
                continue

            contractor = Contractor(
                api_key=secrets.token_hex(32),
                **data,
            )
            session.add(contractor)
            await session.flush()
            seeded_contractors.append(contractor)
            print(f"  [new]  {name} created.")

        await session.commit()
        for c in seeded_contractors:
            await session.refresh(c)

    # Seed leads
    async with SessionFactory() as session:
        for contractor in seeded_contractors:
            leads_data = DEMO_LEADS.get(contractor.name, [])
            for lead_data in leads_data:
                # Idempotency check: phone + contractor_id
                result = await session.execute(
                    select(Lead).where(
                        Lead.phone == lead_data["phone"],
                        Lead.contractor_id == contractor.id,
                    )
                )
                existing_lead = result.scalar_one_or_none()
                if existing_lead is not None:
                    continue

                hours_ago = lead_data.pop("hours_ago")
                lead = Lead(
                    contractor_id=contractor.id,
                    call_id=f"demo_{secrets.token_hex(8)}",
                    created_at=datetime.now(timezone.utc) - timedelta(hours=hours_ago),
                    **lead_data,
                )
                session.add(lead)

        await session.commit()

    # Print formatted table
    print()
    print(f"{'Name':<30} {'ID':<36} {'API Key (last 8)':<16}")
    print("-" * 85)
    for c in seeded_contractors:
        print(f"{c.name:<30} {str(c.id):<36} ...{c.api_key[-8:]}")

    print()
    print("Full API keys (store securely):")
    for c in seeded_contractors:
        print(f"  {c.name}: {c.api_key}")


if __name__ == "__main__":
    asyncio.run(seed())
