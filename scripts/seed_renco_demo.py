"""
One-time script: create the Renco Enterprise demo contractor row in the DB.
The Retell agent + phone number already exist.

Run with:  railway run python3 scripts/seed_renco_demo.py
"""
import asyncio
import secrets
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

RENCO_PHONE    = "+15878001544"
RENCO_AGENT_ID = "agent_7432c3df2dc4af9b91adea5ec4"
RENCO_NAME     = "Renco Enterprise"
RENCO_AGENT    = "Alex"


async def main():
    from app.database import async_session_factory as AsyncSessionLocal
    from app.models.contractor import Contractor
    from app.utils.auth import hash_password
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        # Check if already exists
        result = await db.execute(
            select(Contractor).where(Contractor.phone_number == RENCO_PHONE)
        )
        existing = result.scalar_one_or_none()
        if existing:
            print(f"✅ Contractor already exists: id={existing.id} name={existing.name}")
            return

        contractor = Contractor(
            name=RENCO_NAME,
            agent_name=RENCO_AGENT,
            email=f"renco-demo-{secrets.token_hex(4)}@tradesflowos.internal",
            hashed_password=hash_password(secrets.token_hex(32)),
            api_key=secrets.token_hex(32),
            trades=["hvac", "plumbing", "electrical"],
            service_areas=["Calgary, AB", "Edmonton, AB"],
            phone_number=RENCO_PHONE,
            retell_agent_id=RENCO_AGENT_ID,
            is_active=True,
            is_verified=True,
            plan="pro",
            sms_enabled=False,
            calls_this_month=0,
            sms_this_month=0,
            calendar_provider="manual",
            calendar_config={},
            diagnostic_fee=0.0,
            free_estimate=True,
            timezone="America/Edmonton",
        )
        db.add(contractor)
        await db.commit()
        await db.refresh(contractor)
        print(f"✅ Renco Enterprise demo contractor created")
        print(f"   id:        {contractor.id}")
        print(f"   phone:     {contractor.phone_number}")
        print(f"   agent_id:  {contractor.retell_agent_id}")
        print(f"\n👉  If you want this treated as a demo line (daily cap etc.),")
        print(f"    set DEMO_CONTRACTOR_ID_2={contractor.id} in Railway.")


if __name__ == "__main__":
    asyncio.run(main())
