"""The agent's job queue: rows in the agentrun table (CMPS271-236).

No extra service: the API adds a queued row, and the worker claims the oldest one with a
conditional UPDATE, so two workers can never take the same run. Runs, their field entries and
screenshots are written as they happen, so the audit trail survives a worker crash.
"""

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import update
from sqlmodel import Session, delete, select

from ..models import AgentFieldEntry, AgentRun, AgentScreenshot, Application
from ..notifications import notify_agent_run

ACTIVE = ("queued", "running")
# A run still "running" this long after it started belongs to a worker that died.
STALE_AFTER = timedelta(minutes=15)


class RunCancelled(Exception):
    """The traveler cancelled the run, or deleted its application, while it was being filled."""


def now() -> datetime:
    return datetime.now(timezone.utc)


def active_run(session: Session, application_id: str) -> AgentRun | None:
    return session.exec(select(AgentRun).where(AgentRun.application_id == application_id,
                                               AgentRun.status.in_(ACTIVE))).first()


def enqueue(session: Session, application: Application, site_id: str) -> AgentRun:
    run = AgentRun(id=uuid.uuid4().hex, traveler_id=application.traveler_id, application_id=application.id,
                   site=site_id, status="queued")
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def claim(session: Session) -> AgentRun | None:
    """Take the oldest queued run for this worker, or None. Safe with several workers."""
    while True:
        candidate = session.exec(select(AgentRun.id).where(AgentRun.status == "queued")
                                 .order_by(AgentRun.created_at, AgentRun.id).limit(1)).first()
        if candidate is None:
            return None
        taken = session.execute(update(AgentRun).where(AgentRun.id == candidate, AgentRun.status == "queued")
                                .values(status="running", started_at=now()))
        session.commit()
        if taken.rowcount:
            return session.get(AgentRun, candidate)


def _still_running(session: Session, run_id: str) -> None:
    session.expire_all()
    run = session.get(AgentRun, run_id)
    if run is None or run.status != "running":
        raise RunCancelled()


def record_entry(session: Session, run_id: str, *, page: int, page_title: str, field: str, value: str) -> None:
    _still_running(session, run_id)
    session.add(AgentFieldEntry(run_id=run_id, page=page, page_title=page_title[:100], field=field[:120], value=value))
    session.commit()


def record_screenshot(session: Session, run_id: str, *, page: int, page_title: str) -> str:
    _still_running(session, run_id)
    shot = AgentScreenshot(id=uuid.uuid4().hex, run_id=run_id, page=page, page_title=page_title[:100])
    session.add(shot)
    session.commit()
    return shot.id


def finish(session: Session, run_id: str, status: str, message: str) -> AgentRun | None:
    """Record how the run ended and tell the traveler, in one commit. A cancelled or deleted run stays as it is."""
    session.expire_all()
    run = session.get(AgentRun, run_id)
    if run is None or run.status != "running":
        return run
    run.status, run.message, run.finished_at = status, message[:300], now()
    session.add(run)
    application = session.get(Application, run.application_id)
    if application is not None:
        notify_agent_run(session, application, None if status == "ready" else message)
    session.commit()
    return run


def cancel(session: Session, run: AgentRun) -> AgentRun:
    if run.status in ACTIVE:
        run.status, run.message, run.finished_at = "cancelled", "You cancelled this run.", now()
        session.add(run)
        session.commit()
    return run


def recover_stale(session: Session) -> int:
    """Fail runs a dead worker left behind, so the traveler can start again."""
    stale = session.exec(select(AgentRun).where(AgentRun.status == "running",
                                                AgentRun.started_at < now() - STALE_AFTER)).all()
    for run in stale:
        run.status, run.message, run.finished_at = "failed", "the agent was interrupted. Please try again", now()
        session.add(run)
    session.commit()
    return len(stale)


def delete_application_runs(session: Session, application_id: str) -> None:
    """Remove an application's runs and their audit rows, children first. The caller commits."""
    run_ids = select(AgentRun.id).where(AgentRun.application_id == application_id)
    session.exec(delete(AgentFieldEntry).where(AgentFieldEntry.run_id.in_(run_ids)))
    session.exec(delete(AgentScreenshot).where(AgentScreenshot.run_id.in_(run_ids)))
    session.exec(delete(AgentRun).where(AgentRun.application_id == application_id))


def delete_traveler_runs(session: Session, traveler_id: int) -> None:
    run_ids = select(AgentRun.id).where(AgentRun.traveler_id == traveler_id)
    session.exec(delete(AgentFieldEntry).where(AgentFieldEntry.run_id.in_(run_ids)))
    session.exec(delete(AgentScreenshot).where(AgentScreenshot.run_id.in_(run_ids)))
    session.exec(delete(AgentRun).where(AgentRun.traveler_id == traveler_id))
