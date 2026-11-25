"""Which notification emails a traveler wants (CMPS271-99).

Three switches, each covering some notification kinds. The bell inside the app always shows every
notification; a switch that is off only stops the email copy.
"""

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict
from sqlmodel import Session

from .features import require_feature
from .models import NotificationPreference, Traveler

# The switch that decides whether each notification kind is emailed. Unknown kinds count as updates.
CATEGORY_OF_KIND = {
    "status_changed": "application_updates",
    "agent_finished": "application_updates",
    "agent_stopped": "application_updates",
    "application_stalled": "reminders",
    "passport_expiring": "passport_expiry",
}


def category(kind: str) -> str:
    return CATEGORY_OF_KIND.get(kind, "application_updates")


def wants_email(session: Session, traveler_id: int, kind: str) -> bool:
    preference = session.get(NotificationPreference, traveler_id)
    return preference is None or getattr(preference, category(kind))


class NotificationPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid")
    application_updates: bool
    reminders: bool
    passport_expiry: bool


def create_notification_preferences_router(get_session, current_traveler) -> APIRouter:
    from .auth import check_origin

    router = APIRouter(prefix="/profile/notification-preferences", tags=["Notifications"],
                       dependencies=[Depends(require_feature("email_notifications"))])
    SessionDep = Annotated[Session, Depends(get_session)]
    TravelerDep = Annotated[Traveler, Depends(current_traveler)]

    def public(row: NotificationPreference | None) -> NotificationPreferences:
        row = row or NotificationPreference(traveler_id=0)
        return NotificationPreferences(application_updates=row.application_updates, reminders=row.reminders,
                                       passport_expiry=row.passport_expiry)

    @router.get("", response_model=NotificationPreferences)
    def get_preferences(traveler: TravelerDep, session: SessionDep, response: Response):
        """The signed-in traveler's email switches. Every email is on until they change it."""
        response.headers["Cache-Control"] = "private, no-store"
        return public(session.get(NotificationPreference, traveler.id))

    @router.put("", response_model=NotificationPreferences, dependencies=[Depends(check_origin)])
    def save_preferences(body: NotificationPreferences, traveler: TravelerDep, session: SessionDep,
                         response: Response):
        """Replace all three switches."""
        response.headers["Cache-Control"] = "private, no-store"
        row = session.get(NotificationPreference, traveler.id) or NotificationPreference(traveler_id=traveler.id)
        row.application_updates, row.reminders, row.passport_expiry = (
            body.application_updates, body.reminders, body.passport_expiry)
        row.updated_at = datetime.now(timezone.utc)
        session.add(row)
        session.commit()
        return public(row)

    return router
