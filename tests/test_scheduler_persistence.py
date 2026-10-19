"""One-off jobs (follow-up texts, reminders) survive a restart."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.models.scheduled_job import ScheduledJob
from app.services import scheduler


def _factory(db):
    class _Ctx:
        async def __aenter__(self_inner):
            return db
        async def __aexit__(self_inner, *a):
            return False
    return lambda: _Ctx()


@pytest.fixture()
def clean_scheduler():
    for job in scheduler._scheduler.get_jobs():
        job.remove()
    yield scheduler._scheduler
    for job in scheduler._scheduler.get_jobs():
        job.remove()


@pytest.mark.asyncio
async def test_job_is_saved_and_restored_after_restart(db, clean_scheduler):
    with patch("app.database.async_session_factory", _factory(db)):
        scheduler.schedule_lead_followup("lead-1", "contractor-1")
        await asyncio.sleep(0.1)
        row = (await db.execute(select(ScheduledJob).where(ScheduledJob.id == "lead_followup_lead-1"))).scalar_one()
        assert row.status == "pending" and row.func_name == "_lead_followup_job"
        assert row.kwargs == {"lead_id": "lead-1", "contractor_id": "contractor-1"}

        clean_scheduler.get_job("lead_followup_lead-1").remove()   # what a restart does to the in-memory store
        assert clean_scheduler.get_job("lead_followup_lead-1") is None
        assert await scheduler.restore_pending_jobs() == 1
        job = clean_scheduler.get_job("lead_followup_lead-1")
        assert job is not None and job.kwargs == {"lead_id": "lead-1", "contractor_id": "contractor-1"}


@pytest.mark.asyncio
async def test_overdue_job_runs_soon_and_stale_job_expires(db, clean_scheduler):
    now = datetime.now(tz=timezone.utc)
    db.add_all([
        ScheduledJob(id="lead_followup_due", func_name="_lead_followup_job", run_at=now - timedelta(hours=2),
                     kwargs={"lead_id": "a", "contractor_id": "b"}),
        ScheduledJob(id="lead_followup_old", func_name="_lead_followup_job", run_at=now - timedelta(days=10),
                     kwargs={"lead_id": "c", "contractor_id": "d"}),
        ScheduledJob(id="mystery", func_name="no_such_function", run_at=now + timedelta(hours=1), kwargs={}),
    ])
    await db.commit()
    with patch("app.database.async_session_factory", _factory(db)):
        assert await scheduler.restore_pending_jobs() == 1
    due = clean_scheduler.get_job("lead_followup_due")
    assert due is not None and due.trigger.run_date <= now + timedelta(seconds=30)  # overdue: runs right after restart
    assert clean_scheduler.get_job("lead_followup_old") is None
    statuses = {r.id: r.status for r in (await db.execute(select(ScheduledJob))).scalars().all()}
    assert statuses["lead_followup_old"] == "expired" and statuses["mystery"] == "expired"


@pytest.mark.asyncio
async def test_finished_job_row_is_closed(db):
    db.add(ScheduledJob(id="lead_followup_x", func_name="_lead_followup_job", run_at=datetime.now(tz=timezone.utc), kwargs={}))
    await db.commit()
    with patch("app.database.async_session_factory", _factory(db)):
        class Ev:
            job_id = "lead_followup_x"
            exception = None
        scheduler._on_job_finished(Ev())
        await asyncio.sleep(0.1)
    assert (await db.execute(select(ScheduledJob.status).where(ScheduledJob.id == "lead_followup_x"))).scalar_one() == "done"
