"""Ask the agent to fill the embassy form, follow the run, and read its audit trail (CMPS271-67, CMPS271-70).

Under /applications/{application_id}/agent, owner-checked like every application route and hidden
behind the `agent_form_filling` flag. Starting a run is the traveler's explicit request; the agent
never submits, so there is no route that could. While `payments` is on, a run is the step payment
unlocks (CMPS271-108): starting one before paying answers 402.
"""

from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlmodel import Session, select

from ..applications import flow
from ..applications.router import is_recorded
from ..features import require_feature
from ..models import AgentFieldEntry, AgentRun, AgentScreenshot, Application, ApplicationAnswer, Traveler
from ..payments.gate import PAYMENT_REQUIRED, payment_due
from . import queue
from .sites import site_for
from .storage import AgentFiles


class SitePublic(BaseModel):
    id: str
    name: str
    is_demo: bool = False


class RunPublic(BaseModel):
    id: str
    site: str
    status: str
    message: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class EntryPublic(BaseModel):
    page: int
    page_title: str
    field: str
    value: str
    entered_at: datetime


class ScreenshotPublic(BaseModel):
    id: str
    page: int
    page_title: str
    taken_at: datetime


class RunDetail(RunPublic):
    entries: list[EntryPublic]
    screenshots: list[ScreenshotPublic]


class AgentState(BaseModel):
    site: SitePublic | None
    # Whether a new run may start now, and if not, why (for the button's explanation).
    can_start: bool
    reason: str | None
    payment_required: bool = False
    runs: list[RunPublic]


def _public(run: AgentRun) -> RunPublic:
    return RunPublic(id=run.id, site=run.site, status=run.status, message=run.message, created_at=run.created_at,
                     started_at=run.started_at, finished_at=run.finished_at)


def blocker(session: Session, application: Application) -> str | None:
    if site_for(application.questionnaire_id) is None:
        return "The agent can't fill this destination's online form yet."
    if application.archived_at is not None:
        return "This application is archived. Restore it first."
    if is_recorded(application):
        return "This application is recorded as submitted."
    questionnaire = flow.load_questionnaire(application.questionnaire_id)
    rows = session.exec(select(ApplicationAnswer).where(ApplicationAnswer.application_id == application.id))
    answers, _ = flow.reconcile(questionnaire, {row.question_id: row.value for row in rows}, date.today())
    if flow.stop_message(questionnaire, answers) or flow.next_question(questionnaire, answers) is not None:
        return "Answer every question first: the agent fills the form only from your confirmed answers."
    if queue.active_run(session, application.id):
        return "The agent is already filling this form."
    return None


def create_agent_router(get_session, current_traveler, files: AgentFiles) -> APIRouter:
    from ..auth import check_origin

    router = APIRouter(prefix="/applications/{application_id}/agent", tags=["Form-filling agent"],
                       dependencies=[Depends(require_feature("agent_form_filling"))])
    SessionDep = Annotated[Session, Depends(get_session)]
    TravelerDep = Annotated[Traveler, Depends(current_traveler)]

    def owned(session: Session, traveler: Traveler, application_id: str) -> Application:
        application = session.get(Application, application_id)
        if application is None or application.traveler_id != traveler.id:
            raise HTTPException(status_code=404, detail="Application not found.")
        return application

    def owned_run(session: Session, application: Application, run_id: str) -> AgentRun:
        run = session.get(AgentRun, run_id)
        if run is None or run.application_id != application.id:
            raise HTTPException(status_code=404, detail="Agent run not found.")
        return run

    def runs(session: Session, application: Application) -> list[AgentRun]:
        return list(session.exec(select(AgentRun).where(AgentRun.application_id == application.id)
                                 .order_by(AgentRun.created_at.desc(), AgentRun.id)).all())

    @router.get("", response_model=AgentState)
    def state(application_id: str, traveler: TravelerDep, session: SessionDep, request: Request, response: Response):
        application = owned(session, traveler, application_id)
        site = site_for(application.questionnaire_id)
        reason = blocker(session, application)
        unpaid = reason is None and payment_due(request.app.state.feature_flags, session, application)
        response.headers["Cache-Control"] = "private, no-store"
        return AgentState(site=SitePublic(id=site.id, name=site.name, is_demo=site.start_url.startswith("file:")) if site else None, can_start=reason is None and not unpaid,
                          reason=PAYMENT_REQUIRED if unpaid else reason, payment_required=unpaid,
                          runs=[_public(run) for run in runs(session, application)])

    @router.post("/runs", response_model=RunPublic, status_code=201, dependencies=[Depends(check_origin)])
    def start(application_id: str, traveler: TravelerDep, session: SessionDep, request: Request, response: Response):
        application = owned(session, traveler, application_id)
        reason = blocker(session, application)
        if reason:
            raise HTTPException(status_code=409, detail=reason)
        if payment_due(request.app.state.feature_flags, session, application):
            raise HTTPException(status_code=402, detail=PAYMENT_REQUIRED)
        response.headers["Cache-Control"] = "private, no-store"
        return _public(queue.enqueue(session, application, site_for(application.questionnaire_id).id))

    @router.get("/runs/{run_id}", response_model=RunDetail)
    def detail(application_id: str, run_id: str, traveler: TravelerDep, session: SessionDep, response: Response):
        run = owned_run(session, owned(session, traveler, application_id), run_id)
        entries = session.exec(select(AgentFieldEntry).where(AgentFieldEntry.run_id == run.id)
                               .order_by(AgentFieldEntry.id)).all()
        shots = session.exec(select(AgentScreenshot).where(AgentScreenshot.run_id == run.id)
                             .order_by(AgentScreenshot.page, AgentScreenshot.taken_at)).all()
        response.headers["Cache-Control"] = "private, no-store"
        return RunDetail(**_public(run).model_dump(),
                         entries=[EntryPublic(page=e.page, page_title=e.page_title, field=e.field, value=e.value,
                                              entered_at=e.entered_at) for e in entries],
                         screenshots=[ScreenshotPublic(id=s.id, page=s.page, page_title=s.page_title,
                                                       taken_at=s.taken_at) for s in shots])

    @router.post("/runs/{run_id}/cancel", response_model=RunPublic, dependencies=[Depends(check_origin)])
    def cancel(application_id: str, run_id: str, traveler: TravelerDep, session: SessionDep, response: Response):
        run = owned_run(session, owned(session, traveler, application_id), run_id)
        response.headers["Cache-Control"] = "private, no-store"
        return _public(queue.cancel(session, run))

    @router.get("/runs/{run_id}/screenshots/{screenshot_id}")
    def screenshot(application_id: str, run_id: str, screenshot_id: str, traveler: TravelerDep, session: SessionDep):
        application = owned(session, traveler, application_id)
        run = owned_run(session, application, run_id)
        shot = session.get(AgentScreenshot, screenshot_id)
        if shot is None or shot.run_id != run.id:
            raise HTTPException(status_code=404, detail="Screenshot not found.")
        png = files.load(traveler.id, application.id, shot.id)
        if png is None:
            raise HTTPException(status_code=404, detail="Screenshot not found.")
        return Response(png, media_type="image/png", headers={
            "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})

    return router
