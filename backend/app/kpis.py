"""Usage and API effectiveness against the project KPIs (CMPS271-140), for admins.

Aggregates only, over a range of at most MAX_DAYS days: counts, rates, a median and totals per destination,
never a name, email, account or application id or an answer. Each figure comes from a grouped query over the
activity log, the applications or the agent's runs. Traveler accounts only, since staff testing and support
work would skew them; API errors count every signed-in request that failed, whoever made it.

Definitions. Active: any event travelers caused themselves (not a failed sign-in, an admin's change to their
account or someone opening their documents). Funnel: the applications started in the range and how far they
have got since: answers complete, recorded as submitted, decided. Time to complete: from starting to the
answer that completed the questionnaire. Agent rates are of finished runs (ready, stopped or failed).
"""

from datetime import date, datetime, time, timedelta, timezone
from statistics import median
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, model_validator
from sqlalchemy import and_, case, or_
from sqlmodel import Session, func, select

from .admin_api import require_role
from .agent.filler import FORM_CHANGED
from .applications import flow
from .features import require_feature
from .models import RECORDED_STATUSES, ActivityEvent, AgentRun, Application, ApplicationAnswer, Traveler

MAX_DAYS = 180
DEFAULT_DAYS = 30
COMPLETE = ("ANSWERS_COMPLETE", *RECORDED_STATUSES)
DECIDED = ("APPROVED", "REFUSED")
FINISHED = ("ready", "stopped", "failed")
RUN_STATUSES = ("queued", "running", *FINISHED, "cancelled")
NOT_THEIRS = ("role_change", "status_change", "document_access")


class KpiRange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: date | None = None
    end: date | None = None

    @model_validator(mode="after")
    def bounded(self) -> "KpiRange":
        self.end = self.end or datetime.now(timezone.utc).date()
        self.start = self.start or self.end - timedelta(days=DEFAULT_DAYS - 1)
        if self.start > self.end:
            raise ValueError("The start date must be on or before the end date.")
        if (self.end - self.start).days + 1 > MAX_DAYS:
            raise ValueError(f"Choose a range of at most {MAX_DAYS} days.")
        return self


class DayCount(BaseModel):
    day: date
    count: int


class WeekCount(BaseModel):
    """`week_start` is the week's Monday; only its days inside the range are counted."""
    week_start: date
    count: int


class UsageKpis(BaseModel):
    """Sign-in failures are wrong passwords for existing accounts: unknown emails have no account to record them on."""
    active_users: int
    average_daily_active: float
    daily_active: list[DayCount]
    weekly_active: list[WeekCount]
    registrations: int
    registrations_by_method: dict[str, int]
    sign_ins: int
    sign_in_successes: int
    sign_in_failures: int
    sign_ins_suspended: int
    sign_in_success_rate: float | None
    sign_in_failure_rate: float | None


class FunnelStage(BaseModel):
    """`conversion` is of the stage before; None for the first stage or when the stage before is empty."""
    stage: Literal["started", "completed", "submitted", "decided"]
    count: int
    conversion: float | None


class ApplicationKpis(BaseModel):
    started: int
    completed: int
    submitted: int
    decided: int
    approved: int
    refused: int
    ineligible: int
    funnel: list[FunnelStage]
    median_hours_to_complete: float | None


class AgentKpis(BaseModel):
    runs: int
    ready: int
    stopped: int
    site_changed: int
    failed: int
    cancelled: int
    in_progress: int
    success_rate: float | None
    failure_rate: float | None
    site_changed_rate: float | None


class AssistantKpis(BaseModel):
    messages: int
    answered: int
    proposals: int
    drafts: int
    unavailable: int
    unavailable_rate: float | None
    users: int


class DestinationKpis(BaseModel):
    questionnaire_id: str
    title: str
    started: int
    completed: int
    submitted: int
    decided: int
    agent_runs: int
    agent_ready: int


class Kpis(BaseModel):
    start: date
    end: date
    days: int
    usage: UsageKpis
    applications: ApplicationKpis
    agent: AgentKpis
    assistant: AssistantKpis
    api_errors: int
    api_errors_daily: list[DayCount]
    destinations: list[DestinationKpis]


def rate(part: int, whole: int) -> float | None:
    return round(part / whole, 4) if whole else None


def _day(value) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _sum(counts: dict[str, int], keys) -> int:
    return sum(count for key, count in counts.items() if key in keys)


def _merge(groups: dict[str, dict[str, int]]) -> dict[str, int]:
    merged: dict[str, int] = {}
    for counts in groups.values():
        for key, count in counts.items():
            merged[key] = merged.get(key, 0) + count
    return merged


class _Range:
    def __init__(self, start: date, end: date):
        self.start, self.end = start, end
        self.since = datetime.combine(start, time.min, timezone.utc)
        self.until = datetime.combine(end + timedelta(days=1), time.min, timezone.utc)
        self.days = [start + timedelta(days=offset) for offset in range((end - start).days + 1)]
        self.weeks = [day for day in self.days if day == start or day.weekday() == 0]

    def holds(self, column):
        return and_(column >= self.since, column < self.until)


def _usage(session: Session, period: _Range):
    travelers = Traveler.role == "traveler"
    events = select(ActivityEvent.traveler_id, ActivityEvent.occurred_at).join(
        Traveler, Traveler.id == ActivityEvent.traveler_id).where(
        travelers, period.holds(ActivityEvent.occurred_at), ActivityEvent.event_type.not_in(NOT_THEIRS),
        or_(ActivityEvent.event_type != "sign_in", ActivityEvent.outcome == "success")).subquery()
    people = func.count(func.distinct(events.c.traveler_id))
    day = func.date(events.c.occurred_at).label("day")
    daily = {_day(value): count for value, count in session.exec(select(day, people).group_by("day")).all()}
    week_ends = [datetime.combine(week + timedelta(days=7 - week.weekday()), time.min, timezone.utc)
                 for week in period.weeks]
    week = case(*[(events.c.occurred_at < end, index) for index, end in enumerate(week_ends)],
                else_=len(week_ends) - 1).label("week")
    weekly = dict(session.exec(select(week, people).group_by("week")).all())

    def counted(*columns):
        return select(*columns, func.count()).join(Traveler, Traveler.id == ActivityEvent.traveler_id).where(
            travelers, period.holds(ActivityEvent.occurred_at))

    outcomes: dict[str, dict[str, int]] = {}
    for event_type, outcome, count in session.exec(
            counted(ActivityEvent.event_type, ActivityEvent.outcome)
            .where(ActivityEvent.event_type.in_(("sign_in", "assistant_message")))
            .group_by(ActivityEvent.event_type, ActivityEvent.outcome)).all():
        outcomes.setdefault(event_type, {})[outcome] = count
    registrations = dict(session.exec(counted(ActivityEvent.identifier).where(
        ActivityEvent.event_type == "registration").group_by(ActivityEvent.identifier)).all())
    assistant_users = session.exec(
        select(func.count(func.distinct(ActivityEvent.traveler_id))).join(Traveler, Traveler.id == ActivityEvent.traveler_id)
        .where(travelers, period.holds(ActivityEvent.occurred_at), ActivityEvent.event_type == "assistant_message")).one()

    sign_ins = outcomes.get("sign_in", {})
    total = sum(sign_ins.values())
    usage = UsageKpis(
        active_users=session.exec(select(people)).one(),
        average_daily_active=round(sum(daily.values()) / len(period.days), 2),
        daily_active=[DayCount(day=day, count=daily.get(day, 0)) for day in period.days],
        weekly_active=[WeekCount(week_start=week - timedelta(days=week.weekday()), count=weekly.get(index, 0))
                       for index, week in enumerate(period.weeks)],
        registrations=sum(registrations.values()), registrations_by_method=registrations,
        sign_ins=total, sign_in_successes=sign_ins.get("success", 0), sign_in_failures=sign_ins.get("failure", 0),
        sign_ins_suspended=sign_ins.get("suspended", 0),
        sign_in_success_rate=rate(sign_ins.get("success", 0), total),
        sign_in_failure_rate=rate(sign_ins.get("failure", 0), total),
    )
    said = outcomes.get("assistant_message", {})
    messages = sum(said.values())
    assistant = AssistantKpis(
        messages=messages, answered=said.get("success", 0), proposals=said.get("proposal", 0),
        drafts=said.get("draft", 0), unavailable=said.get("unavailable", 0),
        unavailable_rate=rate(said.get("unavailable", 0), messages), users=assistant_users,
    )
    return usage, assistant


def _applications(session: Session, period: _Range):
    started = and_(period.holds(Application.created_at), Traveler.role == "traveler")
    statuses: dict[str, dict[str, int]] = {}
    for questionnaire_id, status, count in session.exec(
            select(Application.questionnaire_id, Application.status, func.count())
            .join(Traveler, Traveler.id == Application.traveler_id).where(started)
            .group_by(Application.questionnaire_id, Application.status)).all():
        statuses.setdefault(questionnaire_id, {})[status] = count
    last_answer = (select(ApplicationAnswer.application_id, func.max(ApplicationAnswer.answered_at).label("done"))
                   .group_by(ApplicationAnswer.application_id).subquery())
    hours = [(_utc(done) - _utc(created)).total_seconds() / 3600 for created, done in session.exec(
        select(Application.created_at, last_answer.c.done)
        .join(last_answer, last_answer.c.application_id == Application.id)
        .join(Traveler, Traveler.id == Application.traveler_id)
        .where(started, Application.status.in_(COMPLETE))).all()]

    every = _merge(statuses)
    stages = [("started", sum(every.values())), ("completed", _sum(every, COMPLETE)),
              ("submitted", _sum(every, RECORDED_STATUSES)), ("decided", _sum(every, DECIDED))]
    funnel = [FunnelStage(stage=stage, count=count, conversion=rate(count, stages[index - 1][1]) if index else None)
              for index, (stage, count) in enumerate(stages)]
    kpis = ApplicationKpis(
        started=stages[0][1], completed=stages[1][1], submitted=stages[2][1], decided=stages[3][1],
        approved=every.get("APPROVED", 0), refused=every.get("REFUSED", 0), ineligible=every.get("INELIGIBLE", 0),
        funnel=funnel, median_hours_to_complete=round(median(hours), 2) if hours else None,
    )
    return kpis, statuses


def _agent(session: Session, period: _Range):
    changed = case((and_(AgentRun.status == "stopped", AgentRun.message.startswith(FORM_CHANGED)), 1),
                   else_=0).label("changed")
    runs: dict[str, dict[str, int]] = {}
    for questionnaire_id, status, site_changed, count in session.exec(
            select(Application.questionnaire_id, AgentRun.status, changed, func.count())
            .join(Application, Application.id == AgentRun.application_id)
            .join(Traveler, Traveler.id == AgentRun.traveler_id)
            .where(Traveler.role == "traveler", period.holds(AgentRun.created_at))
            .group_by(Application.questionnaire_id, AgentRun.status, "changed")).all():
        found = runs.setdefault(questionnaire_id, {})
        found[status] = found.get(status, 0) + count
        if site_changed:
            found["site_changed"] = found.get("site_changed", 0) + count
    every = _merge(runs)
    finished = _sum(every, FINISHED)
    kpis = AgentKpis(
        runs=_sum(every, RUN_STATUSES), ready=every.get("ready", 0), stopped=every.get("stopped", 0),
        site_changed=every.get("site_changed", 0), failed=every.get("failed", 0),
        cancelled=every.get("cancelled", 0), in_progress=_sum(every, ("queued", "running")),
        success_rate=rate(every.get("ready", 0), finished), failure_rate=rate(every.get("failed", 0), finished),
        site_changed_rate=rate(every.get("site_changed", 0), finished),
    )
    return kpis, runs


def compute(session: Session, start: date, end: date) -> Kpis:
    period = _Range(start, end)
    usage, assistant = _usage(session, period)
    applications, statuses = _applications(session, period)
    agent, runs = _agent(session, period)
    day = func.date(ActivityEvent.occurred_at).label("day")
    errors = {_day(value): count for value, count in session.exec(
        select(day, func.count()).where(ActivityEvent.event_type == "api_error", period.holds(ActivityEvent.occurred_at))
        .group_by("day")).all()}
    destinations = []
    for questionnaire in flow.available_questionnaires():
        found, ran = statuses.get(questionnaire.id, {}), runs.get(questionnaire.id, {})
        destinations.append(DestinationKpis(
            questionnaire_id=questionnaire.id, title=questionnaire.title, started=sum(found.values()),
            completed=_sum(found, COMPLETE), submitted=_sum(found, RECORDED_STATUSES), decided=_sum(found, DECIDED),
            agent_runs=_sum(ran, RUN_STATUSES), agent_ready=ran.get("ready", 0)))
    return Kpis(start=start, end=end, days=len(period.days), usage=usage, applications=applications, agent=agent,
                assistant=assistant, api_errors=sum(errors.values()),
                api_errors_daily=[DayCount(day=day, count=errors.get(day, 0)) for day in period.days],
                destinations=destinations)


def create_kpi_router(get_session, current_traveler) -> APIRouter:
    admin = require_role(current_traveler, "admin")
    router = APIRouter(prefix="/admin/kpis", tags=["Admin KPIs"],
                       dependencies=[Depends(require_feature("kpi_dashboard")), Depends(admin)])

    @router.get("", response_model=Kpis)
    def kpis(period: Annotated[KpiRange, Query()], session: Annotated[Session, Depends(get_session)]):
        return compute(session, period.start, period.end)

    return router
