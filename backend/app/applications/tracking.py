"""After the answers: how to hand the application in, the appointment and what to bring (CMPS271-72/73/77/79).

- The manual submission guide (CMPS271-72) lists, for each destination, the steps the traveler takes themself, from
  `submission_guides.json` and the fee and decision time in `visa_guides.json`. Each step is ticked from what the
  application already shows: answers complete, documents uploaded, appointment recorded, submission and decision
  recorded. Nothing here submits anything.
- The traveler records the appointment (CMPS271-79); reminders go out a week and a day before, through
  `notifications.notify` (fixed sentences, no dates).
- The appointment day checklist (CMPS271-73) is built from the questionnaire's document list and the uploads.
- The stage (CMPS271-77) moves on by itself as these things happen, and the timeline shows each of them.
"""
from datetime import date, datetime, timedelta, timezone
from functools import cache
import json
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlmodel import Session, select

from ..models import AgentRun, Application, ApplicationTracking, SupportingDocument, Traveler
from ..notifications import application_name, notify
from . import flow

GUIDES_FILE = Path(__file__).with_name("submission_guides.json")
PRIVATE = {"Cache-Control": "private, no-store"}
DECIDED = ("APPROVED", "REFUSED")
RECORDED = ("SUBMITTED", *DECIDED)
# Reminders before the appointment: (days before, message). Closest last; no dates in the text.
APPOINTMENT_REMINDERS = {
    7: "Your {name} appointment is in a week. Check the appointment day checklist on the application page.",
    1: "Your {name} appointment is tomorrow. Bring everything on the appointment day checklist.",
}


class Link(BaseModel):
    label: str
    url: str


class GuideSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    form: str
    appointment: bool
    booking: str
    booking_link: Link
    hand_in: str
    collect: str


@cache
def submission_guides() -> dict[str, GuideSource]:
    data = json.loads(GUIDES_FILE.read_text(encoding="utf-8"))
    guides = {key: GuideSource.model_validate(value) for key, value in data["guides"].items()}
    for guide in guides.values():
        if not guide.booking_link.url.startswith("https://"):
            raise ValueError("Submission guide links must be https.")
    return guides


class Step(BaseModel):
    id: str
    title: str
    text: str
    link: Link | None = None
    done: bool


class Appointment(BaseModel):
    needed: bool
    at: datetime | None
    location: str | None


class ChecklistItem(BaseModel):
    label: str
    detail: str | None = None
    # "ready": uploaded or prepared here; "missing": a required upload isn't there yet; "bring": the traveler's own.
    status: Literal["ready", "missing", "bring"]
    optional: bool = False


class Submission(BaseModel):
    stage: str
    steps: list[Step]
    current: str | None
    appointment: Appointment
    checklist: list[ChecklistItem]
    read_only: bool


class AppointmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    at: datetime
    location: str | None = Field(default=None, max_length=200)

    @field_validator("at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Give the time with its time zone.")
        return value.astimezone(timezone.utc)

    @field_validator("location")
    @classmethod
    def trimmed(cls, value: str | None) -> str | None:
        return (value or "").strip() or None


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def tracking_row(session: Session, application: Application) -> ApplicationTracking | None:
    return session.get(ApplicationTracking, application.id)


def _uploaded(session: Session, application: Application) -> set[str]:
    return set(session.exec(select(SupportingDocument.requirement).where(
        SupportingDocument.application_id == application.id)).all())


def _agent_ready(session: Session, application: Application) -> datetime | None:
    finished = session.exec(select(AgentRun.finished_at).where(
        AgentRun.application_id == application.id, AgentRun.status == "ready")).all()
    return max((_utc(value) for value in finished if value), default=None)


def stage(application: Application, tracking: ApplicationTracking | None, agent_ready: datetime | None,
          answers_complete: bool) -> str:
    """Where the application stands, moving on by itself as things happen (CMPS271-77)."""
    if application.status in DECIDED:
        return "Decision recorded"
    if application.status == "SUBMITTED":
        return "Handed in, waiting for the decision"
    if tracking and tracking.appointment_at:
        return "Appointment booked"
    if agent_ready or (tracking and tracking.packet_downloaded_at):
        return "Ready to hand in"
    if answers_complete:
        return "Preparing documents"
    return "Answering questions"


def build_submission(session: Session, application: Application, questionnaire: flow.Questionnaire,
                     answers: flow.Answers, answers_complete: bool) -> Submission:
    from .visa_guides import load_guides

    guide = submission_guides().get(questionnaire.id)
    visa = next((item for item in load_guides() if item.id == questionnaire.id), None)
    tracking = tracking_row(session, application)
    uploaded = _uploaded(session, application)
    required = flow.required_documents(questionnaire, answers)
    documents_done = all(document.id in uploaded for document in required if document.required)
    recorded = application.status in RECORDED
    appointment_at = _utc(tracking.appointment_at) if tracking else None

    steps = [Step(id="answers", title="Finish your answers",
                  text="Answer every question here, then run the check before you submit.",
                  done=answers_complete or recorded)]
    if guide:
        fee = ""
        if visa and visa.fees.items:
            fee = f" The fee is {visa.fees.items[0].amount} ({visa.fees.items[0].label.lower()})."
        steps += [
            Step(id="form", title="Prepare the official form", text=guide.form, done=recorded or bool(_agent_ready(session, application))),
            Step(id="documents", title="Gather your documents",
                 text="Upload each document on the checklist, then download the print-ready packet to bring them in order.",
                 done=recorded or (documents_done and bool(tracking and tracking.packet_downloaded_at))),
            Step(id="book", title="Book an appointment" if guide.appointment else "Pay and send the application",
                 text=guide.booking + fee, link=guide.booking_link,
                 done=recorded or bool(appointment_at) if guide.appointment else recorded),
            Step(id="hand_in", title="Hand it in", text=guide.hand_in, done=recorded),
            Step(id="decision", title="Wait for the decision",
                 text=" ".join(part for part in ((visa.time.text if visa else ""), guide.collect,
                                                 "Record the decision on your dashboard when it arrives.") if part),
                 done=application.status in DECIDED),
        ]
    current = next((step.id for step in steps if not step.done), None)
    return Submission(
        stage=stage(application, tracking, _agent_ready(session, application), answers_complete),
        steps=steps, current=current,
        appointment=Appointment(needed=bool(guide and guide.appointment), at=appointment_at,
                                location=tracking.appointment_location if tracking else None),
        checklist=checklist(questionnaire, answers, uploaded, guide),
        read_only=application.archived_at is not None or application.status in DECIDED,
    )


def checklist(questionnaire: flow.Questionnaire, answers: flow.Answers, uploaded: set[str],
              guide: GuideSource | None) -> list[ChecklistItem]:
    """What to bring on the appointment day (CMPS271-73): the passport, the form, then each document."""
    items = [ChecklistItem(label="Your passport", status="bring",
                           detail="The original, signed, with at least two blank pages."),
             ChecklistItem(label="A copy of your passport's photo page", status="bring")]
    if questionnaire.destination == "SCHENGEN":
        items.append(ChecklistItem(label="The application form, printed and signed", status="bring",
                                   detail="Download the filled form on this page; sign it by hand."))
    elif guide and not guide.appointment:
        items.append(ChecklistItem(label="Your printed e-Visa", status="bring",
                                   detail="Carry it with your passport when you travel."))
    else:
        items.append(ChecklistItem(label="The confirmation page of your online application", status="bring",
                                   detail="Print it after you submit the online form."))
    for document in flow.required_documents(questionnaire, answers):
        items.append(ChecklistItem(label=document.label, detail=document.help, optional=not document.required,
                                   status="ready" if document.id in uploaded else "bring" if not document.required
                                   else "missing"))
    items.append(ChecklistItem(label="Proof you paid the visa fee", status="bring",
                               detail="Or the means to pay it there, if the centre takes payment on the day."))
    if guide and guide.appointment:
        items.append(ChecklistItem(label="Your appointment confirmation", status="bring"))
    items.append(ChecklistItem(label="The print-ready packet from Ta'asheera", status="bring",
                               detail="Your uploaded documents in the order the embassy lists them."))
    return items


def record_packet_download(session: Session, application: Application) -> None:
    """Remember the first packet download, which moves the stage on (CMPS271-77). The caller commits."""
    row = tracking_row(session, application) or ApplicationTracking(application_id=application.id,
                                                                    traveler_id=application.traveler_id)
    if row.packet_downloaded_at is None:
        row.packet_downloaded_at = datetime.now(timezone.utc)
        session.add(row)


def timeline_events(session: Session, application: Application) -> list[tuple[str, datetime, str]]:
    """(kind, when, text) for the timeline: the packet, the agent's filled form and the appointment."""
    events = []
    tracking = tracking_row(session, application)
    if tracking and tracking.packet_downloaded_at:
        events.append(("prepared", _utc(tracking.packet_downloaded_at), "Downloaded the print-ready packet"))
    ready = _agent_ready(session, application)
    if ready:
        events.append(("prepared", ready, "The agent filled the embassy form, waiting for your review"))
    return events


def next_step_text(session: Session, application: Application, today: date) -> str | None:
    """What comes next once the answers are complete, from the appointment and packet (CMPS271-77)."""
    tracking = tracking_row(session, application)
    if tracking and tracking.appointment_at and _utc(tracking.appointment_at).date() >= today:
        return ("Your embassy appointment is booked. Bring everything on the appointment day checklist, and "
                "record the submission here once you've handed it in.")
    if tracking and tracking.appointment_at:
        return "Your appointment date has passed. If you handed the application in, record the submission here."
    if tracking and tracking.packet_downloaded_at:
        return "Your packet is ready. Book your appointment and record it here, so you get reminders."
    return None


def remind_appointments(engine, *, now: datetime | None = None) -> int:
    """Add the appointment reminders that are due, a week and a day before. Returns how many were added."""
    now = now or datetime.now(timezone.utc)
    added = 0
    with Session(engine) as session:
        rows = session.exec(select(ApplicationTracking).where(ApplicationTracking.appointment_at.is_not(None))).all()
        for row in rows:
            remaining = _utc(row.appointment_at) - now
            if remaining <= timedelta(0):
                continue
            level = 1 if remaining <= timedelta(days=1, hours=12) else 7 if remaining <= timedelta(days=7) else None
            if level is None or (row.appointment_reminded_days is not None and row.appointment_reminded_days <= level):
                continue
            application = session.get(Application, row.application_id)
            owner = session.get(Traveler, row.traveler_id)
            if (application is None or application.archived_at is not None or application.status in DECIDED
                    or owner is None or owner.status != "active"):
                continue
            reminder = notify(session, row.traveler_id, "appointment_reminder",
                              APPOINTMENT_REMINDERS[level].format(name=application_name(application)),
                              application_id=application.id, link=f"/dashboard/{application.id}#submit-yourself")
            reminder.created_at = now
            row.appointment_reminded_days = level
            session.add(row)
            session.commit()
            added += 1
    return added


def add_tracking_routes(router: APIRouter, get_session, current_traveler, *, today=date.today) -> None:
    """Routes under /applications/{id}: the submission guide with checklist, and the appointment."""
    from ..auth import check_origin
    from ..features import require_feature
    from .router import _owned_answers, _status, current_answers

    SessionDep = Annotated[Session, Depends(get_session)]
    TravelerDep = Annotated[Traveler, Depends(current_traveler)]

    def load(session: Session, traveler: Traveler, application_id: str):
        owned = _owned_answers(session, traveler.id, application_id)
        if owned is None:
            raise HTTPException(status_code=404, detail="Application not found.")
        application, questionnaire, stored = owned
        answers, _ = current_answers(questionnaire, application, stored, today())
        complete = application.status in RECORDED or _status(questionnaire, answers) == "ANSWERS_COMPLETE"
        return application, questionnaire, answers, complete

    @router.get("/{application_id}/submission", response_model=Submission,
                dependencies=[Depends(require_feature("submission_tracking"))])
    def submission(application_id: str, traveler: TravelerDep, session: SessionDep, response: Response):
        response.headers.update(PRIVATE)
        application, questionnaire, answers, complete = load(session, traveler, application_id)
        return build_submission(session, application, questionnaire, answers, complete)

    @router.put("/{application_id}/appointment", response_model=Submission, dependencies=[Depends(check_origin), Depends(require_feature("submission_tracking"))])
    def record_appointment(application_id: str, data: AppointmentRequest, traveler: TravelerDep,
                           session: SessionDep, response: Response):
        response.headers.update(PRIVATE)
        application, questionnaire, answers, complete = load(session, traveler, application_id)
        guide = submission_guides().get(questionnaire.id)
        if guide is None or not guide.appointment:
            raise HTTPException(status_code=409, detail="This visa needs no appointment.")
        if application.archived_at is not None or application.status in DECIDED:
            raise HTTPException(status_code=409, detail="This application can't change any more.")
        now = datetime.now(timezone.utc)
        if not now < data.at <= now + timedelta(days=400):
            raise HTTPException(status_code=422, detail="Choose an appointment time in the future, within the next year.")
        row = tracking_row(session, application) or ApplicationTracking(application_id=application.id,
                                                                        traveler_id=traveler.id)
        if _utc(row.appointment_at) != data.at:
            row.appointment_reminded_days = None
        row.appointment_at, row.appointment_location = data.at, data.location
        session.add(row)
        session.commit()
        return build_submission(session, application, questionnaire, answers, complete)

    @router.delete("/{application_id}/appointment", response_model=Submission, dependencies=[Depends(check_origin), Depends(require_feature("submission_tracking"))])
    def remove_appointment(application_id: str, traveler: TravelerDep, session: SessionDep, response: Response):
        response.headers.update(PRIVATE)
        application, questionnaire, answers, complete = load(session, traveler, application_id)
        if application.archived_at is not None or application.status in DECIDED:
            raise HTTPException(status_code=409, detail="This application can't change any more.")
        row = tracking_row(session, application)
        if row is not None:
            row.appointment_at = row.appointment_location = row.appointment_reminded_days = None
            session.add(row)
            session.commit()
        return build_submission(session, application, questionnaire, answers, complete)
