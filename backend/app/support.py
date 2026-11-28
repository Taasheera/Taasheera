"""Support with the traveler's consent (CMPS271-128) and talking to a person (CMPS271-92).

Travelers (`/support`): open a help request (the assistant's "Talk to a person"), follow their
requests, and let support read one application for 24 hours, 72 hours or 7 days, or stop it.

Staff (`/admin/support`, support and admin roles): the request queue, and an application its owner
shared, read only and only while the grant lasts: answers as shown, the completeness check and the
timeline, never passport images or document files. Every read is written to the owner's document
access history as "Support" before it is returned. Without a current grant the application is a 404.
"""

from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import update
from sqlmodel import Session, func, select

from .activity import record_activity
from .admin_api import require_role
from .applications import completeness, flow
from .applications.lifecycle import Timeline, build_timeline
from .applications.router import ApplicationSummary, _status, _summary, _utc, check_day, current_answers, is_recorded
from .features import require_feature
from .models import Application, ApplicationAnswer, DocumentAccess, Role, SupportAccessGrant, SupportRequest, Traveler
from .notifications import notify_support_request
from .security import throttle
from .support_requests import MESSAGE_LIMIT, REPLY_LIMIT, SupportRequestError, create_support_request, plain_text

GrantHours = Literal[24, 72, 168]
SupportStatus = Literal["open", "in_progress", "resolved"]
RowId = Annotated[int, Path(gt=0, le=2_147_483_647)]
APPLICATION_ID = r"^[0-9a-f]{32}$"
NOT_SHARED = "Application not found."
REQUEST_NOT_FOUND = "Support request not found."
EXCERPT = 120

DocumentIssues = Callable[[Session, Application, flow.Answers], list[completeness.Issue] | None]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _optional_utc(value: datetime | None) -> datetime | None:
    return _utc(value) if value else None


def active_grant(session: Session, application_id: str, now: datetime) -> SupportAccessGrant | None:
    """The grant letting support read this application now, if any: not revoked and not expired."""
    return session.exec(select(SupportAccessGrant).where(
        SupportAccessGrant.application_id == application_id, SupportAccessGrant.revoked_at.is_(None),
        SupportAccessGrant.expires_at > now).order_by(SupportAccessGrant.expires_at.desc())).first()


def grant_access(session: Session, application: Application, hours: int, now: datetime) -> SupportAccessGrant:
    """Let support read this application for `hours`, replacing a grant that is still running."""
    session.execute(update(SupportAccessGrant).where(
        SupportAccessGrant.application_id == application.id, SupportAccessGrant.revoked_at.is_(None),
        SupportAccessGrant.expires_at > now).values(revoked_at=now))
    grant = SupportAccessGrant(traveler_id=application.traveler_id, application_id=application.id,
                               created_at=now, expires_at=now + timedelta(hours=hours))
    session.add(grant)
    return grant


class SupportRequestPublic(BaseModel):
    """A help request as its traveler sees it: no staff names and no staff notes."""

    id: int
    application_id: str | None
    source: str
    message: str
    status: SupportStatus
    reply: str | None
    created_at: datetime
    updated_at: datetime
    replied_at: datetime | None
    resolved_at: datetime | None


class NewSupportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=MESSAGE_LIMIT)
    application_id: str | None = Field(default=None, pattern=APPLICATION_ID)
    share_hours: GrantHours | None = None


class AccessGrantPublic(BaseModel):
    id: int
    application_id: str
    title: str
    created_at: datetime
    expires_at: datetime


class NewAccessGrant(BaseModel):
    model_config = ConfigDict(extra="forbid")
    application_id: str = Field(pattern=APPLICATION_ID)
    hours: GrantHours


def request_public(row: SupportRequest) -> SupportRequestPublic:
    return SupportRequestPublic(id=row.id, application_id=row.application_id, source=row.source, message=row.message,
                                status=row.status, reply=row.reply, created_at=_utc(row.created_at),
                                updated_at=_utc(row.updated_at), replied_at=_optional_utc(row.replied_at),
                                resolved_at=_optional_utc(row.resolved_at))


def grant_public(grant: SupportAccessGrant, application: Application) -> AccessGrantPublic:
    return AccessGrantPublic(id=grant.id, application_id=application.id,
                             title=flow.load_questionnaire(application.questionnaire_id).title,
                             created_at=_utc(grant.created_at), expires_at=_utc(grant.expires_at))


def create_support_router(get_session, current_traveler) -> APIRouter:
    from .auth import check_origin

    router = APIRouter(prefix="/support", tags=["Support"], dependencies=[Depends(require_feature("support_tools"))])
    SessionDep = Annotated[Session, Depends(get_session)]
    TravelerDep = Annotated[Traveler, Depends(current_traveler)]

    def owned(session: Session, traveler: Traveler, application_id: str) -> Application:
        application = session.get(Application, application_id)
        if application is None or application.traveler_id != traveler.id:
            raise HTTPException(status_code=404, detail="Application not found.")
        return application

    def recorded(session: Session, request: Request, traveler: Traveler, event: str, identifier: str) -> None:
        if record_activity(session, request.app.state.activity_directory, traveler.id, event, identifier,
                           "success") is None:
            raise HTTPException(status_code=401, detail="Your account is no longer available.")

    @router.get("/requests", response_model=list[SupportRequestPublic])
    def my_requests(traveler: TravelerDep, session: SessionDep, response: Response):
        """The signed-in traveler's help requests, newest first."""
        response.headers["Cache-Control"] = "private, no-store"
        rows = session.exec(select(SupportRequest).where(SupportRequest.traveler_id == traveler.id)
                            .order_by(SupportRequest.created_at.desc(), SupportRequest.id.desc()).limit(50)).all()
        return [request_public(row) for row in rows]

    @router.post("/requests", response_model=SupportRequestPublic, status_code=201, dependencies=[Depends(check_origin)])
    def open_request(data: NewSupportRequest, request: Request, traveler: TravelerDep, session: SessionDep,
                     response: Response):
        """Ask a person for help; optionally let support read the named application for a while."""
        response.headers["Cache-Control"] = "private, no-store"
        if data.share_hours is not None and data.application_id is None:
            raise HTTPException(status_code=422, detail="Choose the application support may read.")
        application = owned(session, traveler, data.application_id) if data.application_id else None
        throttle(request, session, "support-request", account=str(traveler.id), account_limit=5)
        try:
            created = create_support_request(session, traveler.id, source="website", message=data.message,
                                             application_id=data.application_id)
        except SupportRequestError as error:
            raise HTTPException(status_code=422, detail=str(error)) from None
        if application is not None and data.share_hours is not None:
            grant_access(session, application, data.share_hours, _now())
        recorded(session, request, traveler, "support_request", "website")
        session.refresh(created)
        return request_public(created)

    @router.get("/access", response_model=list[AccessGrantPublic])
    def my_grants(traveler: TravelerDep, session: SessionDep, response: Response):
        """The applications support may read right now, and until when."""
        response.headers["Cache-Control"] = "private, no-store"
        rows = session.exec(select(SupportAccessGrant, Application).join(Application).where(
            SupportAccessGrant.traveler_id == traveler.id, SupportAccessGrant.revoked_at.is_(None),
            SupportAccessGrant.expires_at > _now()).order_by(SupportAccessGrant.expires_at)).all()
        return [grant_public(grant, application) for grant, application in rows]

    @router.post("/access", response_model=AccessGrantPublic, status_code=201, dependencies=[Depends(check_origin)])
    def share(data: NewAccessGrant, request: Request, traveler: TravelerDep, session: SessionDep, response: Response):
        """Let support read one application, read only, for 24 hours, 72 hours or 7 days."""
        response.headers["Cache-Control"] = "private, no-store"
        application = owned(session, traveler, data.application_id)
        grant = grant_access(session, application, data.hours, _now())
        recorded(session, request, traveler, "support_access", "granted")
        session.refresh(grant)
        return grant_public(grant, application)

    @router.delete("/access/{grant_id}", status_code=204, response_class=Response, dependencies=[Depends(check_origin)])
    def stop_sharing(grant_id: RowId, request: Request, traveler: TravelerDep, session: SessionDep):
        """Stop support reading an application at once."""
        grant = session.get(SupportAccessGrant, grant_id)
        if grant is None or grant.traveler_id != traveler.id:
            raise HTTPException(status_code=404, detail="Access not found.")
        if grant.revoked_at is None:
            grant.revoked_at = _now()
            session.add(grant)
        recorded(session, request, traveler, "support_access", "revoked")
        return Response(status_code=204, headers={"Cache-Control": "private, no-store"})

    return router


class StaffProfile(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    email: str
    role: Role


class RequestSummary(BaseModel):
    id: int
    traveler_name: str
    source: str
    status: SupportStatus
    excerpt: str
    application_id: str | None
    assignee_name: str | None
    assigned_to_me: bool
    created_at: datetime
    updated_at: datetime


class RequestPage(BaseModel):
    items: list[RequestSummary]
    total: int
    page: int
    page_size: int


class RequestDetail(RequestSummary):
    message: str
    reply: str | None
    notes: str | None
    replied_at: datetime | None
    resolved_at: datetime | None
    access_expires_at: datetime | None


class RequestSearch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: SupportStatus | None = None
    mine: bool = False
    page: int = Field(default=1, ge=1, le=10_000)
    page_size: int = Field(default=20, ge=1, le=100)


class ReplyChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reply: str = Field(min_length=1, max_length=REPLY_LIMIT)


class NotesChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    notes: str = Field(max_length=REPLY_LIMIT)


class StatusChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: SupportStatus


class SharedApplication(BaseModel):
    application_id: str
    title: str
    traveler_name: str
    expires_at: datetime


class StaffAnswer(BaseModel):
    question_id: str
    step: str
    prompt: str
    type: flow.AnswerType
    options: list[flow.Option] | None
    value: Any
    source: str


class StaffIssue(BaseModel):
    kind: completeness.Kind
    severity: completeness.Severity
    message: str
    question_id: str | None = None
    step_id: str | None = None


class StaffCheck(BaseModel):
    ready: bool
    answered: int
    total: int
    issues: list[StaffIssue]
    readiness_score: int
    readiness_reason: str


class StaffApplicationView(BaseModel):
    """What support may read of a shared application: never passport images, document files or the chat."""

    application: ApplicationSummary
    traveler_name: str
    access_expires_at: datetime
    answers: list[StaffAnswer]
    check: StaffCheck
    timeline: Timeline


def no_document_issues(session: Session, application: Application, answers: flow.Answers) -> None:
    return None


def create_staff_support_router(get_session, current_traveler, *, document_issues: DocumentIssues = no_document_issues,
                                today: Callable[[], date] = date.today) -> APIRouter:
    """`document_issues` must read upload records only, never the files themselves."""
    from .auth import check_origin

    staff = require_role(current_traveler, "support", "admin")
    router = APIRouter(prefix="/admin/support", tags=["Support staff"],
                       dependencies=[Depends(require_feature("support_tools")), Depends(staff)])
    SessionDep = Annotated[Session, Depends(get_session)]
    StaffDep = Annotated[Traveler, Depends(staff)]

    def names(session: Session, ids: set[int]) -> dict[int, str]:
        return dict(session.exec(select(Traveler.id, Traveler.name).where(Traveler.id.in_(sorted(ids)))).all()) if ids else {}

    def summary_fields(row: SupportRequest, people: dict[int, str], signed_in: Traveler) -> dict:
        return dict(id=row.id, traveler_name=people.get(row.traveler_id, "Traveler"), source=row.source,
                    status=row.status, excerpt=" ".join(row.message.split())[:EXCERPT],
                    application_id=row.application_id,
                    assignee_name=people.get(row.assignee_id) if row.assignee_id else None,
                    assigned_to_me=row.assignee_id == signed_in.id,
                    created_at=_utc(row.created_at), updated_at=_utc(row.updated_at))

    def detail(session: Session, row: SupportRequest, signed_in: Traveler) -> RequestDetail:
        people = names(session, {row.traveler_id, *([row.assignee_id] if row.assignee_id else [])})
        grant = active_grant(session, row.application_id, _now()) if row.application_id else None
        return RequestDetail(**summary_fields(row, people, signed_in), message=row.message, reply=row.reply,
                             notes=row.notes, replied_at=_optional_utc(row.replied_at),
                             resolved_at=_optional_utc(row.resolved_at),
                             access_expires_at=_utc(grant.expires_at) if grant else None)

    def load(session: Session, request_id: int) -> SupportRequest:
        row = session.get(SupportRequest, request_id)
        if row is None:
            raise HTTPException(status_code=404, detail=REQUEST_NOT_FOUND)
        return row

    def save(session: Session, row: SupportRequest) -> None:
        row.updated_at = _now()
        session.add(row)
        session.commit()
        session.refresh(row)

    @router.get("/me", response_model=StaffProfile)
    def staff_me(signed_in: StaffDep):
        return signed_in

    @router.get("/requests", response_model=RequestPage)
    def queue(search: Annotated[RequestSearch, Query()], signed_in: StaffDep, session: SessionDep):
        filters = []
        if search.status is not None:
            filters.append(SupportRequest.status == search.status)
        if search.mine:
            filters.append(SupportRequest.assignee_id == signed_in.id)
        rows = session.exec(select(SupportRequest).where(*filters)
                            .order_by(SupportRequest.created_at.desc(), SupportRequest.id.desc())
                            .offset((search.page - 1) * search.page_size).limit(search.page_size)).all()
        people = names(session, {row.traveler_id for row in rows} | {row.assignee_id for row in rows if row.assignee_id})
        total = session.exec(select(func.count()).select_from(SupportRequest).where(*filters)).one()
        return RequestPage(items=[RequestSummary(**summary_fields(row, people, signed_in)) for row in rows],
                           total=total, page=search.page, page_size=search.page_size)

    @router.get("/requests/{request_id}", response_model=RequestDetail)
    def get_request(request_id: RowId, signed_in: StaffDep, session: SessionDep):
        return detail(session, load(session, request_id), signed_in)

    @router.post("/requests/{request_id}/assign", response_model=RequestDetail, dependencies=[Depends(check_origin)])
    def assign_to_me(request_id: RowId, signed_in: StaffDep, session: SessionDep):
        row = load(session, request_id)
        row.assignee_id = signed_in.id
        if row.status == "open":
            row.status = "in_progress"
        save(session, row)
        return detail(session, row, signed_in)

    @router.put("/requests/{request_id}/reply", response_model=RequestDetail, dependencies=[Depends(check_origin)])
    def reply(request_id: RowId, data: ReplyChange, signed_in: StaffDep, session: SessionDep):
        """Answer the traveler. They get a fixed-sentence notification and read the reply on their account page."""
        row = load(session, request_id)
        try:
            text = plain_text(data.reply, REPLY_LIMIT)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from None
        if not text:
            raise HTTPException(status_code=422, detail="Write a reply first.")
        row.reply, row.replied_at = text, _now()
        if row.assignee_id is None:
            row.assignee_id = signed_in.id
        if row.status == "open":
            row.status = "in_progress"
        notify_support_request(session, row, "support_replied")
        save(session, row)
        return detail(session, row, signed_in)

    @router.put("/requests/{request_id}/notes", response_model=RequestDetail, dependencies=[Depends(check_origin)])
    def notes(request_id: RowId, data: NotesChange, signed_in: StaffDep, session: SessionDep):
        """Notes for staff. The traveler doesn't see them on the site; their data download includes them."""
        row = load(session, request_id)
        try:
            row.notes = plain_text(data.notes, REPLY_LIMIT) or None
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from None
        save(session, row)
        return detail(session, row, signed_in)

    @router.put("/requests/{request_id}/status", response_model=RequestDetail, dependencies=[Depends(check_origin)])
    def change_status(request_id: RowId, data: StatusChange, signed_in: StaffDep, session: SessionDep):
        row = load(session, request_id)
        if row.status != data.status:
            row.status = data.status
            row.resolved_at = _now() if data.status == "resolved" else None
            if data.status == "resolved":
                notify_support_request(session, row, "support_resolved")
            save(session, row)
        return detail(session, row, signed_in)

    @router.get("/shared", response_model=list[SharedApplication])
    def shared(session: SessionDep):
        """Applications travelers let support read right now."""
        rows = session.exec(select(SupportAccessGrant, Application, Traveler.name)
                            .join(Application, Application.id == SupportAccessGrant.application_id)
                            .join(Traveler, Traveler.id == Application.traveler_id)
                            .where(SupportAccessGrant.revoked_at.is_(None), SupportAccessGrant.expires_at > _now())
                            .order_by(SupportAccessGrant.expires_at)).all()
        return [SharedApplication(application_id=application.id, traveler_name=name, expires_at=_utc(grant.expires_at),
                                  title=flow.load_questionnaire(application.questionnaire_id).title)
                for grant, application, name in rows]

    @router.get("/applications/{application_id}", response_model=StaffApplicationView)
    def view_application(application_id: str, request: Request, signed_in: StaffDep, session: SessionDep):
        """A shared application, read only. 404 without a current grant, as if it didn't exist."""
        grant = active_grant(session, application_id, _now())
        application = session.get(Application, application_id) if grant else None
        if application is None or application.traveler_id != grant.traveler_id:
            raise HTTPException(status_code=404, detail=NOT_SHARED)
        day = today()
        questionnaire = flow.load_questionnaire(application.questionnaire_id)
        rows = {row.question_id: row for row in session.exec(
            select(ApplicationAnswer).where(ApplicationAnswer.application_id == application.id))}
        answers, _ = current_answers(questionnaire, application, {key: row.value for key, row in rows.items()}, day)
        summary = ApplicationSummary(**_summary(application, questionnaire, answers))
        if not is_recorded(application):
            summary.status = _status(questionnaire, answers)
        view = StaffApplicationView(
            application=summary,
            traveler_name=session.get(Traveler, application.traveler_id).name,
            access_expires_at=_utc(grant.expires_at),
            answers=[StaffAnswer(question_id=question.id, step=questionnaire.step_of(question.id).title,
                                 prompt=question.prompt, type=question.type, options=question.options,
                                 value=answers[question.id], source=rows[question.id].source)
                     for question in flow.visible_questions(questionnaire, answers) if question.id in answers],
            check=StaffCheck.model_validate(completeness.check(
                questionnaire, answers, check_day(application, day),
                document_issues=document_issues(session, application, answers)).model_dump()),
            timeline=build_timeline(session, application, day),
        )
        session.add(DocumentAccess(traveler_id=application.traveler_id, actor_id=signed_in.id,
                                   upload_id=application.id, purpose="support_view"))
        if record_activity(session, request.app.state.activity_directory, application.traveler_id,
                           "document_access", "support", "success") is None:
            raise HTTPException(status_code=404, detail=NOT_SHARED)
        return view

    return router
