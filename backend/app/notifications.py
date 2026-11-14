"""In-app notifications (US 28): one place that tells a traveler something happened to an application.

Call a helper in the same database session as the change it reports, before that session commits, so
the change and its notification are saved together or not at all:

- `notify_status_change(session, application, previous)` after setting `application.status`
  (the applications router does this; recording an embassy decision, CMPS271-80, is one more call).
- `notify_agent_run(session, application, stopped_reason=None)` when the form-filling agent
  finishes or stops (CMPS271-67).

Messages are fixed sentences about the traveler's own application. Never put passport fields,
answer values or other personal data in them. Email copies wait until the reset email bug
(CMPS271-233) proves mail reaches every address.
"""

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlmodel import Session, select

from .applications.flow import load_questionnaire
from .models import Application, Notification, Traveler

MESSAGE_LIMIT = 300

# What each status means to the traveler. {name} is the application's title.
STATUS_MESSAGES = {
    "DRAFT": "Your {name} application has questions left to answer. Open it to continue.",
    "INELIGIBLE": "One of your answers stops your {name} application. Open it to see why.",
    "ANSWERS_COMPLETE": "Every question in your {name} application is answered.",
    # Recorded by the traveler (CMPS271-80); the agent never submits.
    "SUBMITTED": "You recorded that your {name} application was submitted to the embassy.",
    "APPROVED": "You recorded that your {name} application was approved.",
    "REFUSED": "You recorded that your {name} application was refused.",
}


def _name(application: Application) -> str:
    try:
        return load_questionnaire(application.questionnaire_id).title
    except KeyError:
        return "visa"


def _utc(value: datetime) -> datetime:
    # SQLite drops timezone information when reading a datetime back.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def notify(session: Session, traveler_id: int, kind: str, message: str, *,
           application_id: str | None = None, link: str | None = None) -> Notification:
    """Add a notification to the session. The caller commits it with the change it reports."""
    notification = Notification(traveler_id=traveler_id, application_id=application_id, kind=kind,
                                message=message[:MESSAGE_LIMIT], link=link)
    session.add(notification)
    return notification


def notify_status_change(session: Session, application: Application, previous: str) -> Notification | None:
    """Tell the owner their application's status changed. Does nothing if it did not change."""
    if application.status == previous:
        return None
    template = STATUS_MESSAGES.get(application.status, "Your {name} application has a new status.")
    return notify(session, application.traveler_id, "status_changed", template.format(name=_name(application)),
                  application_id=application.id, link=f"/dashboard/{application.id}")


def notify_agent_run(session: Session, application: Application, stopped_reason: str | None = None) -> Notification:
    """Tell the owner the agent finished filling the embassy form, or why it stopped.

    `stopped_reason` is a short fixed explanation such as "the site asked for a CAPTCHA",
    never a value typed into the form.
    """
    name = _name(application)
    if stopped_reason is None:
        kind = "agent_finished"
        message = (f"The agent filled the embassy form for your {name} application and stopped before "
                   "submitting. Review it before you submit.")
    else:
        kind = "agent_stopped"
        message = f"The agent stopped filling the embassy form for your {name} application: {stopped_reason}."
    return notify(session, application.traveler_id, kind, message,
                  application_id=application.id, link=f"/dashboard/{application.id}")


class NotificationPublic(BaseModel):
    id: int
    kind: str
    message: str
    link: str | None
    application_id: str | None
    created_at: datetime
    read: bool


def create_notifications_router(get_session, current_traveler) -> APIRouter:
    router = APIRouter(prefix="/notifications", tags=["Notifications"])
    SessionDep = Annotated[Session, Depends(get_session)]
    TravelerDep = Annotated[Traveler, Depends(current_traveler)]

    @router.get("", response_model=list[NotificationPublic])
    def list_notifications(traveler: TravelerDep, session: SessionDep,
                           limit: Annotated[int, Query(ge=1, le=100)] = 50):
        """The signed-in traveler's notifications, newest first. Only their own."""
        rows = session.exec(
            select(Notification).where(Notification.traveler_id == traveler.id)
            .order_by(Notification.created_at.desc(), Notification.id.desc()).limit(limit)
        ).all()
        return [NotificationPublic(id=row.id, kind=row.kind, message=row.message, link=row.link,
                                   application_id=row.application_id, created_at=_utc(row.created_at),
                                   read=row.read_at is not None) for row in rows]

    return router
