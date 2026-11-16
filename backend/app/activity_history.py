"""One UTC activity stream, with Beirut-second filters and bounded pagination."""
from datetime import datetime, timedelta, timezone
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import or_
from sqlmodel import Session, func, select

from .admin_api import UserId, require_role
from .features import require_feature
from .models import ActivityEvent, Traveler

BEIRUT = ZoneInfo("Asia/Beirut")


def install_action_history(app: FastAPI):
    """Fill gaps through the same logger, using only server route templates.

    Existing semantic events remain (for example document_access before content
    is returned). This separate api_action records the result of authenticated
    user API interactions without copying bodies, paths, query strings or fields.
    Routine authentication/polling and history reads do not create more history.
    """
    @app.middleware("http")
    async def record_action(request, call_next):
        response = await call_next(request)
        request.state.activity_response_status = response.status_code
        return response


def record_completed_action(session, request):
    """Called by the session dependency after the handler/response finishes.

    Roll back any unfinished work before logging a rejected request. Recording
    here avoids waiting on a second DB connection while the handler still holds
    a row lock, and never commits a rejected action's pending changes.
    """
    import logging
    from .activity import record_activity
    owner = getattr(request.state, "traveler_id", None)
    flags = getattr(request.app.state, "feature_flags", None)
    route = getattr(request.scope.get("route"), "path", None)
    ignored = {"/auth/me", "/activity/events", "/activity/documents", "/activity/history",
               "/admin/users/{user_id}/activity", "/notifications/unread-count"}
    if owner is None or not flags or not flags.is_on("activity_history") or not route or route in ignored:
        return
    status = getattr(request.state, "activity_response_status", 500)
    outcome = "success" if status < 400 else "denied" if status < 500 else "error"
    try:
        session.rollback()
        record_activity(session, request.app.state.activity_directory, owner, "api_action",
                        f"{request.method} {route}"[:80], outcome)
    except Exception:
        logging.getLogger(__name__).error("Could not persist API activity metadata")


def boundary(value: datetime, *, upper=False) -> datetime:
    """Naive values mean Beirut; aware API values carry their explicit offset.

    For the repeated DST hour, include both occurrences at the selected boundary.
    A nonexistent spring-forward time is refused rather than silently changed.
    """
    if value.year in (1, 9999):
        raise ValueError("Choose a supported calendar year.")
    if value.tzinfo is not None:
        result = value.astimezone(timezone.utc)
    else:
        candidates = []
        for fold in (0, 1):
            utc = value.replace(tzinfo=BEIRUT, fold=fold).astimezone(timezone.utc)
            if utc.astimezone(BEIRUT).replace(tzinfo=None) == value:
                candidates.append(utc)
        if not candidates:
            raise ValueError("That Beirut time does not exist because the clocks change. Choose another time.")
        result = max(candidates) if upper else min(candidates)
    result = result.replace(microsecond=0)
    return result + timedelta(seconds=1) if upper else result


class HistorySearch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    from_at: datetime | None = None
    to_at: datetime | None = None
    page: int = Field(default=1, ge=1, le=100000)
    page_size: int = Field(default=25, ge=1, le=100)
    documents_only: bool = False

    @model_validator(mode="after")
    def dates(self):
        start = boundary(self.from_at) if self.from_at else None
        end = boundary(self.to_at, upper=True) if self.to_at else None
        if start and end and start >= end:
            raise ValueError("From must be before or equal to To.")
        return self


class HistoryEvent(BaseModel):
    id: int
    traveler_id: int
    actor_id: int | None
    actor: str
    occurred_at: datetime
    beirut_time: str
    event_type: str
    identifier: str
    outcome: str
    resource_type: str | None
    resource_id: str | None
    purpose: str | None


class HistoryPage(BaseModel):
    items: list[HistoryEvent]
    total: int
    page: int
    page_size: int
    timezone: str = "Asia/Beirut"


def history(session, owner_id, search, *, documents_only=False, owner_view=False):
    condition = (ActivityEvent.traveler_id == owner_id if owner_view else
                 or_(ActivityEvent.traveler_id == owner_id, ActivityEvent.actor_id == owner_id))
    filters = [condition]
    if documents_only:
        filters.append(ActivityEvent.event_type == "document_access")
    if search.from_at:
        filters.append(ActivityEvent.occurred_at >= boundary(search.from_at))
    if search.to_at:
        filters.append(ActivityEvent.occurred_at < boundary(search.to_at, upper=True))
    rows = session.exec(select(ActivityEvent).where(*filters)
        .order_by(ActivityEvent.occurred_at.desc(), ActivityEvent.id.desc())
        .offset((search.page - 1) * search.page_size).limit(search.page_size)).all()
    items = []
    for row in rows:
        utc = row.occurred_at.replace(tzinfo=timezone.utc) if row.occurred_at.tzinfo is None else row.occurred_at.astimezone(timezone.utc)
        actor_id = row.actor_id
        if row.actor_kind == "service": label = "Automated service"
        elif row.actor_kind == "staff": label = f"Staff account #{actor_id}" if actor_id else "Former staff account"
        elif actor_id == owner_id: label = "You" if owner_view else f"Account #{owner_id}"
        else: label = "Unknown account" if actor_id is None else f"Account #{actor_id}"
        items.append(HistoryEvent(id=row.id, traveler_id=row.traveler_id,
            actor_id=None if owner_view and actor_id != owner_id else actor_id, actor=label,
            occurred_at=utc, beirut_time=utc.astimezone(BEIRUT).isoformat(timespec="seconds"),
            event_type=row.event_type, identifier=row.identifier, outcome=row.outcome,
            resource_type=row.resource_type, resource_id=row.resource_id, purpose=row.purpose))
    return HistoryPage(items=items, total=session.exec(select(func.count()).select_from(ActivityEvent).where(*filters)).one(),
                       page=search.page, page_size=search.page_size)


def create_history_router(get_session, current_traveler):
    router = APIRouter(tags=["Activity history"], dependencies=[Depends(require_feature("activity_history"))])
    admin = require_role(current_traveler, "admin")
    SessionDep = Annotated[Session, Depends(get_session)]

    @router.get("/admin/users/{user_id}/activity", response_model=HistoryPage, dependencies=[Depends(admin)])
    def user_activity(user_id: UserId, search: Annotated[HistorySearch, Query()], response: Response,
                      session: SessionDep, signed_in: Annotated[Traveler, Depends(admin)]):
        if user_id < 1 or session.get(Traveler, user_id) is None:
            raise HTTPException(404, "User not found.")
        response.headers["Cache-Control"] = "no-store"
        return history(session, user_id, search, documents_only=search.documents_only)

    @router.get("/activity/history", response_model=HistoryPage)
    def own_activity(search: Annotated[HistorySearch, Query()], response: Response, session: SessionDep,
                     traveler: Annotated[Traveler, Depends(current_traveler)]):
        response.headers["Cache-Control"] = "no-store"
        return history(session, traveler.id, search, documents_only=search.documents_only, owner_view=True)

    return router
