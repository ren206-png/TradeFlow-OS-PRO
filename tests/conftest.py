import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base
from app.models.contractor import Contractor
from app.models.call import CallSession

# ---------------------------------------------------------------------------
# In-memory SQLite engine for tests
# ---------------------------------------------------------------------------

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture(scope="function")
async def db_engine():
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def db(db_engine):
    session_factory = async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as session:
        yield session


# ---------------------------------------------------------------------------
# Shared model fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def contractor() -> Contractor:
    return Contractor(
        id=uuid.uuid4(),
        name="ABC Plumbing Ltd",
        agent_name="Alex",
        phone_number="+15550001234",
        api_key="test-api-key-abc",
        trades=["plumbing", "hvac"],
        service_areas=["T2N", "T2P", "Calgary"],
        timezone="America/Edmonton",
        diagnostic_fee=99.0,
        free_estimate=False,
        calendar_provider="manual",
        calendar_config={},
        sms_enabled=True,
        review_link="https://g.page/abcplumbing",
        is_active=True,
    )


@pytest.fixture()
def call_session(contractor) -> CallSession:
    return CallSession(
        id=uuid.uuid4(),
        retell_call_id="retell-call-abc123",
        contractor_id=contractor.id,
        status="active",
        conversation_history=[],
    )


@pytest.fixture()
def tool_context(contractor, call_session, db):
    return {
        "contractor": contractor,
        "call_session": call_session,
        "db": db,
    }
