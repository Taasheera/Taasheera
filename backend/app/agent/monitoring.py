"""Operator signals for selector drift and failed runs, without form values or error messages."""
from sqlalchemy import or_
from sqlmodel import Session, select

from ..models import AgentRun
from .filler import FORM_CHANGED


def recent_agent_problems(session: Session, limit: int):
    changed = (AgentRun.status == "stopped") & AgentRun.message.startswith(FORM_CHANGED)
    runs = session.exec(select(AgentRun.site, AgentRun.status, AgentRun.finished_at)
                        .where(or_(changed, AgentRun.status == "failed"), AgentRun.finished_at.is_not(None))
                        .order_by(AgentRun.finished_at.desc(), AgentRun.id).limit(limit)).all()
    return [{"site": site, "kind": "site_changed" if status == "stopped" else "failed", "occurred_at": at}
            for site, status, at in runs]
