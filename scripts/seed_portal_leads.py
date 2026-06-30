"""
Seed script: creates demo leads across ALL trades for the CoolAir HVAC Services
(or any contractor found by name) in the PRODUCTION database.

Usage:
    DATABASE_URL="postgresql://..." python scripts/seed_portal_leads.py

The script is fully idempotent — re-running it skips leads that already exist.
"""
from __future__ import annotations

import asyncio
import os
import secrets
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

DATABASE_URL: str = os.environ.get("DATABASE_URL", "")
if not DATABASE_URL:
    print("ERROR: set DATABASE_URL env var before running this script.")
    sys.exit(1)

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
elif DATABASE_URL.startswith("postgresql://") and "+asyncpg" not in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

engine = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
SessionFactory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

from app.models.contractor import Contractor  # noqa: E402
from app.models.lead import Lead  # noqa: E402

# ---------------------------------------------------------------------------
# Target contractor — change name if needed
# ---------------------------------------------------------------------------
TARGET_CONTRACTOR_NAME = "CoolAir HVAC Services"

# ---------------------------------------------------------------------------
# Demo leads covering every common trade
# ---------------------------------------------------------------------------
DEMO_LEADS = [
    # ── HVAC ──────────────────────────────────────────────────────────────
    {
        "caller_name": "Jennifer Cho",
        "phone": "+14035552001",
        "trade": "hvac",
        "service_category": "AC Repair",
        "property_type": "residential",
        "problem_summary": "AC not cooling at all. House is sitting at 85°F in the summer heat.",
        "emergency_level": "same_day",
        "priority_level": "high",
        "appointment_status": "booked",
        "service_area_status": "inside",
        "call_direction": "inbound",
        "emergency_score": 75,
        "revenue_score": 80,
        "close_probability": 90,
        "customer_sentiment": "negative",
        "service_address": "142 Maple Ave",
        "city": "Calgary",
        "province_state": "AB",
        "postal_zip": "T2E 3K1",
        "ai_summary": "AC failure in summer heat. Same-day service appointment booked. Customer frustrated but cooperative.",
        "hours_ago": 3,
    },
    {
        "caller_name": "Brian Osei",
        "phone": "+14035552002",
        "trade": "heating",
        "service_category": "Furnace Repair",
        "property_type": "residential",
        "problem_summary": "Furnace making loud banging noise on startup every morning.",
        "emergency_level": "flexible",
        "priority_level": "medium",
        "appointment_status": "follow_up",
        "service_area_status": "inside",
        "call_direction": "inbound",
        "emergency_score": 40,
        "revenue_score": 70,
        "close_probability": 75,
        "customer_sentiment": "neutral",
        "service_address": "89 Oak Crescent",
        "city": "Calgary",
        "province_state": "AB",
        "postal_zip": "T2G 4R2",
        "ai_summary": "Furnace startup noise likely delayed ignition. Customer available weekday afternoons.",
        "hours_ago": 12,
    },
    {
        "caller_name": "Diane Marchetti",
        "phone": "+14035552003",
        "trade": "cooling",
        "service_category": "AC Maintenance",
        "property_type": "commercial",
        "problem_summary": "AC unit leaking water inside the office, dripping onto ceiling tiles.",
        "emergency_level": "same_day",
        "priority_level": "high",
        "appointment_status": "contacted",
        "service_area_status": "inside",
        "call_direction": "inbound",
        "emergency_score": 60,
        "revenue_score": 70,
        "close_probability": 80,
        "customer_sentiment": "negative",
        "service_address": "230 Commerce Blvd",
        "city": "Calgary",
        "province_state": "AB",
        "postal_zip": "T2H 1K4",
        "ai_summary": "Commercial AC condensate leak damaging ceiling. Urgently needs same-day repair.",
        "hours_ago": 6,
    },
    {
        "caller_name": "Rachel Simmons",
        "phone": "+14035552005",
        "trade": "hvac",
        "service_category": "Thermostat",
        "property_type": "residential",
        "problem_summary": "Smart thermostat not responding, temperature keeps fluctuating between 65 and 80.",
        "emergency_level": "same_day",
        "priority_level": "medium",
        "appointment_status": "pending",
        "service_area_status": "inside",
        "call_direction": "inbound",
        "emergency_score": 50,
        "revenue_score": 40,
        "close_probability": 60,
        "customer_sentiment": "neutral",
        "service_address": "77 Birch Lane",
        "city": "Calgary",
        "province_state": "AB",
        "postal_zip": "T2G 2N3",
        "ai_summary": "Unresponsive smart thermostat causing wild temperature swings. Diagnostics needed.",
        "hours_ago": 18,
    },
    # ── PLUMBING ──────────────────────────────────────────────────────────
    {
        "caller_name": "Sarah Mitchell",
        "phone": "+14035551001",
        "trade": "plumbing",
        "service_category": "Emergency Pipe Repair",
        "property_type": "residential",
        "problem_summary": "Burst pipe under kitchen sink, water flooding the cabinet and floor.",
        "emergency_level": "emergency",
        "priority_level": "emergency",
        "appointment_status": "booked",
        "service_area_status": "inside",
        "call_direction": "inbound",
        "life_safety_risk": True,
        "emergency_score": 95,
        "revenue_score": 85,
        "close_probability": 95,
        "customer_sentiment": "negative",
        "service_address": "412 Willow Street",
        "city": "Calgary",
        "province_state": "AB",
        "postal_zip": "T2P 1R9",
        "ai_summary": "Emergency burst pipe under kitchen sink. Immediate dispatch required. Water shutoff advised.",
        "hours_ago": 1,
    },
    {
        "caller_name": "Tom Kovacs",
        "phone": "+14035551002",
        "trade": "plumbing",
        "service_category": "Toilet Repair",
        "property_type": "residential",
        "problem_summary": "Toilet running constantly and leaking around the base. Hardwood floor getting damaged.",
        "emergency_level": "same_day",
        "priority_level": "high",
        "appointment_status": "booked",
        "service_area_status": "inside",
        "call_direction": "inbound",
        "emergency_score": 55,
        "revenue_score": 60,
        "close_probability": 85,
        "customer_sentiment": "neutral",
        "service_address": "55 Pine Close",
        "city": "Calgary",
        "province_state": "AB",
        "postal_zip": "T2N 2J7",
        "ai_summary": "Running toilet with base leak risking floor damage. Same-day repair scheduled.",
        "hours_ago": 8,
    },
    {
        "caller_name": "Marcus Webb",
        "phone": "+14035551004",
        "trade": "plumbing",
        "service_category": "Drain Cleaning",
        "property_type": "residential",
        "problem_summary": "Main drain completely blocked, sewage backing up into basement bathroom.",
        "emergency_level": "emergency",
        "priority_level": "emergency",
        "appointment_status": "booked",
        "service_area_status": "inside",
        "call_direction": "inbound",
        "life_safety_risk": True,
        "emergency_score": 98,
        "revenue_score": 90,
        "close_probability": 95,
        "customer_sentiment": "negative",
        "service_address": "18 Cedar Road",
        "city": "Calgary",
        "province_state": "AB",
        "postal_zip": "T2R 0M1",
        "ai_summary": "Sewage backup emergency in basement. Priority dispatch required. Health hazard.",
        "hours_ago": 2,
    },
    {
        "caller_name": "Linda Patel",
        "phone": "+14035551003",
        "trade": "plumbing",
        "service_category": "Water Pressure",
        "property_type": "residential",
        "problem_summary": "Low water pressure throughout the entire house for the past week.",
        "emergency_level": "flexible",
        "priority_level": "low",
        "appointment_status": "pending",
        "service_area_status": "inside",
        "call_direction": "inbound",
        "emergency_score": 25,
        "revenue_score": 50,
        "close_probability": 65,
        "customer_sentiment": "neutral",
        "service_address": "303 Aspen Way",
        "city": "Calgary",
        "province_state": "AB",
        "postal_zip": "T2N 4B3",
        "ai_summary": "Persistent low water pressure, possibly pressure regulator or supply line issue.",
        "hours_ago": 24,
    },
    # ── ELECTRICAL ────────────────────────────────────────────────────────
    {
        "caller_name": "Ahmed Khalil",
        "phone": "+14035553003",
        "trade": "electrical",
        "service_category": "Panel Emergency",
        "property_type": "residential",
        "problem_summary": "Burning smell coming from electrical panel and lights flickering throughout house.",
        "emergency_level": "emergency",
        "priority_level": "emergency",
        "appointment_status": "booked",
        "service_area_status": "inside",
        "call_direction": "inbound",
        "life_safety_risk": True,
        "emergency_score": 99,
        "revenue_score": 90,
        "close_probability": 95,
        "customer_sentiment": "negative",
        "service_address": "64 Spruce Drive",
        "city": "Calgary",
        "province_state": "AB",
        "postal_zip": "T3A 1L4",
        "ai_summary": "Critical fire hazard — burning smell from panel. Immediate emergency dispatch sent.",
        "hours_ago": 1,
    },
    {
        "caller_name": "David Torres",
        "phone": "+14035553001",
        "trade": "electrical",
        "service_category": "Breaker Repair",
        "property_type": "residential",
        "problem_summary": "Kitchen breaker tripping repeatedly, no power to appliances or microwave.",
        "emergency_level": "same_day",
        "priority_level": "high",
        "appointment_status": "booked",
        "service_area_status": "inside",
        "call_direction": "inbound",
        "emergency_score": 65,
        "revenue_score": 65,
        "close_probability": 85,
        "customer_sentiment": "negative",
        "service_address": "91 Fir Blvd",
        "city": "Calgary",
        "province_state": "AB",
        "postal_zip": "T3B 2K8",
        "ai_summary": "Recurring kitchen breaker trips, possible overload or short circuit. Same-day booked.",
        "hours_ago": 4,
    },
    {
        "caller_name": "Priya Nair",
        "phone": "+14035553004",
        "trade": "electrical",
        "service_category": "EV Charger Install",
        "property_type": "residential",
        "problem_summary": "Need a 240V outlet installed in the garage for a new Tesla EV charger.",
        "emergency_level": "flexible",
        "priority_level": "medium",
        "appointment_status": "booked",
        "service_area_status": "inside",
        "call_direction": "inbound",
        "emergency_score": 10,
        "revenue_score": 80,
        "close_probability": 90,
        "customer_sentiment": "positive",
        "service_address": "200 Larch Terrace",
        "city": "Calgary",
        "province_state": "AB",
        "postal_zip": "T3C 3P1",
        "ai_summary": "EV charger install, needs 240V dedicated circuit. High-value job, customer very ready.",
        "hours_ago": 30,
    },
    # ── ROOFING ───────────────────────────────────────────────────────────
    {
        "caller_name": "Greg Hoffman",
        "phone": "+14035554001",
        "trade": "roofing",
        "service_category": "Roof Leak",
        "property_type": "residential",
        "problem_summary": "Active roof leak after hail storm. Water coming through ceiling in master bedroom.",
        "emergency_level": "emergency",
        "priority_level": "emergency",
        "appointment_status": "booked",
        "service_area_status": "inside",
        "call_direction": "inbound",
        "life_safety_risk": False,
        "emergency_score": 88,
        "revenue_score": 85,
        "close_probability": 92,
        "customer_sentiment": "negative",
        "service_address": "511 Redwood Crescent",
        "city": "Calgary",
        "province_state": "AB",
        "postal_zip": "T2E 5V2",
        "ai_summary": "Hail damage causing active bedroom ceiling leak. Emergency tarp and repair booked.",
        "hours_ago": 2,
    },
    {
        "caller_name": "Cynthia Blake",
        "phone": "+14035554002",
        "trade": "roofing",
        "service_category": "Roof Inspection",
        "property_type": "residential",
        "problem_summary": "Selling the house and need a roof inspection report for the buyers.",
        "emergency_level": "flexible",
        "priority_level": "low",
        "appointment_status": "booked",
        "service_area_status": "inside",
        "call_direction": "inbound",
        "emergency_score": 10,
        "revenue_score": 45,
        "close_probability": 88,
        "customer_sentiment": "positive",
        "service_address": "28 Poplar Way",
        "city": "Calgary",
        "province_state": "AB",
        "postal_zip": "T2G 1N6",
        "ai_summary": "Pre-sale roof inspection needed. Quick and easy job. Customer very cooperative.",
        "hours_ago": 48,
    },
    # ── GENERAL CONTRACTING / HANDYMAN ────────────────────────────────────
    {
        "caller_name": "Mike Donnelly",
        "phone": "+14035555001",
        "trade": "general contracting",
        "service_category": "Basement Renovation",
        "property_type": "residential",
        "problem_summary": "Looking to finish an unfinished basement — framing, drywall, flooring, lighting.",
        "emergency_level": "flexible",
        "priority_level": "medium",
        "appointment_status": "pending",
        "service_area_status": "inside",
        "call_direction": "inbound",
        "emergency_score": 5,
        "revenue_score": 95,
        "close_probability": 70,
        "customer_sentiment": "positive",
        "service_address": "177 Hawthorn Gate",
        "city": "Calgary",
        "province_state": "AB",
        "postal_zip": "T2H 2R9",
        "ai_summary": "Full basement finishing project. High revenue potential. Quote requested.",
        "hours_ago": 36,
    },
    {
        "caller_name": "Amy Carson",
        "phone": "+14035555002",
        "trade": "handyman",
        "service_category": "General Repairs",
        "property_type": "residential",
        "problem_summary": "Fence gate broken, two doors won't close properly, and drywall patch needed in hallway.",
        "emergency_level": "flexible",
        "priority_level": "low",
        "appointment_status": "pending",
        "service_area_status": "inside",
        "call_direction": "inbound",
        "emergency_score": 10,
        "revenue_score": 35,
        "close_probability": 80,
        "customer_sentiment": "neutral",
        "service_address": "44 Elm Close",
        "city": "Calgary",
        "province_state": "AB",
        "postal_zip": "T2N 1M2",
        "ai_summary": "Multiple small repairs needed. Good fit for handyman half-day slot.",
        "hours_ago": 20,
    },
    # ── PAINTING ──────────────────────────────────────────────────────────
    {
        "caller_name": "Carla Mendez",
        "phone": "+14035556001",
        "trade": "painting",
        "service_category": "Interior Painting",
        "property_type": "residential",
        "problem_summary": "Want to repaint the entire main floor — living room, dining room, kitchen, hallway.",
        "emergency_level": "flexible",
        "priority_level": "medium",
        "appointment_status": "pending",
        "service_area_status": "inside",
        "call_direction": "inbound",
        "emergency_score": 5,
        "revenue_score": 75,
        "close_probability": 70,
        "customer_sentiment": "positive",
        "service_address": "360 Walnut Blvd",
        "city": "Calgary",
        "province_state": "AB",
        "postal_zip": "T2G 3S4",
        "ai_summary": "Full main-floor interior repaint. Customer wants quote and colour consultation.",
        "hours_ago": 14,
    },
    # ── APPLIANCE REPAIR ──────────────────────────────────────────────────
    {
        "caller_name": "James Fowler",
        "phone": "+14035557001",
        "trade": "appliance repair",
        "service_category": "Refrigerator Repair",
        "property_type": "residential",
        "problem_summary": "Refrigerator stopped cooling overnight. Full of groceries that will spoil.",
        "emergency_level": "same_day",
        "priority_level": "high",
        "appointment_status": "booked",
        "service_area_status": "inside",
        "call_direction": "inbound",
        "emergency_score": 70,
        "revenue_score": 60,
        "close_probability": 88,
        "customer_sentiment": "negative",
        "service_address": "9 Chestnut Ave",
        "city": "Calgary",
        "province_state": "AB",
        "postal_zip": "T2P 2J1",
        "ai_summary": "Fridge failure with perishable food at risk. Same-day tech dispatched.",
        "hours_ago": 5,
    },
    # ── WINDOWS & DOORS ───────────────────────────────────────────────────
    {
        "caller_name": "Helen Yuen",
        "phone": "+14035558001",
        "trade": "windows",
        "service_category": "Window Replacement",
        "property_type": "residential",
        "problem_summary": "Three bedroom windows are cracked and drafty. High heating bills.",
        "emergency_level": "flexible",
        "priority_level": "medium",
        "appointment_status": "pending",
        "service_area_status": "inside",
        "call_direction": "inbound",
        "emergency_score": 20,
        "revenue_score": 85,
        "close_probability": 72,
        "customer_sentiment": "neutral",
        "service_address": "150 Sycamore Lane",
        "city": "Calgary",
        "province_state": "AB",
        "postal_zip": "T2H 4N5",
        "ai_summary": "3 cracked windows causing heat loss. Customer wants quote for energy-efficient replacements.",
        "hours_ago": 28,
    },
    # ── LANDSCAPING ───────────────────────────────────────────────────────
    {
        "caller_name": "Robert Chang",
        "phone": "+14035559001",
        "trade": "landscaping",
        "service_category": "Lawn Care",
        "property_type": "residential",
        "problem_summary": "Need weekly lawn mowing and seasonal yard cleanup — front and back yard.",
        "emergency_level": "flexible",
        "priority_level": "low",
        "appointment_status": "pending",
        "service_area_status": "inside",
        "call_direction": "inbound",
        "emergency_score": 5,
        "revenue_score": 55,
        "close_probability": 78,
        "customer_sentiment": "positive",
        "service_address": "620 Ironwood Dr",
        "city": "Calgary",
        "province_state": "AB",
        "postal_zip": "T2E 7P3",
        "ai_summary": "Recurring weekly lawn service requested. Good recurring revenue opportunity.",
        "hours_ago": 40,
    },
    # ── PEST CONTROL ──────────────────────────────────────────────────────
    {
        "caller_name": "Samantha Price",
        "phone": "+14035550101",
        "trade": "pest control",
        "service_category": "Rodent Removal",
        "property_type": "residential",
        "problem_summary": "Mice in the kitchen and walls. Can hear them at night and found droppings.",
        "emergency_level": "same_day",
        "priority_level": "high",
        "appointment_status": "booked",
        "service_area_status": "inside",
        "call_direction": "inbound",
        "emergency_score": 55,
        "revenue_score": 65,
        "close_probability": 88,
        "customer_sentiment": "negative",
        "service_address": "8 Hornbeam Close",
        "city": "Calgary",
        "province_state": "AB",
        "postal_zip": "T2G 5R8",
        "ai_summary": "Active mouse infestation in kitchen and walls. Customer wants same-day inspection.",
        "hours_ago": 7,
    },
    # ── WATERPROOFING ─────────────────────────────────────────────────────
    {
        "caller_name": "Frank Olsen",
        "phone": "+14035550202",
        "trade": "waterproofing",
        "service_category": "Basement Waterproofing",
        "property_type": "residential",
        "problem_summary": "Basement floods every time it rains heavily. Water seeping through the foundation.",
        "emergency_level": "same_day",
        "priority_level": "high",
        "appointment_status": "follow_up",
        "service_area_status": "inside",
        "call_direction": "inbound",
        "emergency_score": 65,
        "revenue_score": 90,
        "close_probability": 80,
        "customer_sentiment": "negative",
        "service_address": "33 Hornbeam Gate",
        "city": "Calgary",
        "province_state": "AB",
        "postal_zip": "T2H 3M2",
        "ai_summary": "Recurring basement flooding on heavy rain. Major waterproofing job — high revenue.",
        "hours_ago": 10,
    },
]


async def seed() -> None:
    async with SessionFactory() as session:
        # Find the target contractor
        result = await session.execute(
            select(Contractor).where(Contractor.name == TARGET_CONTRACTOR_NAME)
        )
        contractor = result.scalar_one_or_none()

        if contractor is None:
            print(f"ERROR: No contractor named '{TARGET_CONTRACTOR_NAME}' found in the database.")
            print("Available contractors:")
            all_c = await session.execute(select(Contractor))
            for c in all_c.scalars().all():
                print(f"  - {c.name} (id={c.id})")
            sys.exit(1)

        print(f"Found contractor: {contractor.name} (id={contractor.id})")
        print(f"Seeding {len(DEMO_LEADS)} leads across all trades...\n")

        created = 0
        skipped = 0

        for lead_data in DEMO_LEADS:
            # Idempotency: skip if phone + contractor already exists
            result = await session.execute(
                select(Lead).where(
                    Lead.phone == lead_data["phone"],
                    Lead.contractor_id == contractor.id,
                )
            )
            if result.scalar_one_or_none() is not None:
                print(f"  [skip]  {lead_data['caller_name']} ({lead_data['trade']})")
                skipped += 1
                continue

            hours_ago = lead_data.pop("hours_ago")
            lead = Lead(
                contractor_id=contractor.id,
                call_id=f"demo_{secrets.token_hex(8)}",
                lead_source="demo",
                created_at=datetime.now(timezone.utc) - timedelta(hours=hours_ago),
                **lead_data,
            )
            session.add(lead)
            print(f"  [new]   {lead_data['caller_name']} — {lead_data['trade']} ({lead_data['priority_level']})")
            created += 1

        await session.commit()

    print(f"\nDone. Created: {created}  Skipped (already existed): {skipped}")
    print(f"\nOpen the portal to see your leads:")
    print("  https://tradesflowos.com/portal/leads")


if __name__ == "__main__":
    asyncio.run(seed())
