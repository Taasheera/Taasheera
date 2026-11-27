"""FAQ and help content that admins edit without a deploy (CMPS271-129).

Public `GET /help-content?language=en` lists the published entries in order (`/faq` is the page that
shows them, so the API has its own prefix). Admins manage every entry under `/admin/help-content`:
add, edit, publish or unpublish, reorder and delete. Answers are plain text; the page renders
paragraphs, "- " lists and **bold** as text, never as HTML.
"""

from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response
from pydantic import AfterValidator, BaseModel, ConfigDict, Field
from sqlmodel import Session, func, select

from .activity import record_activity
from .admin_api import require_role
from .features import require_feature
from .models import FaqEntry, Traveler
from .support_requests import plain_text

Language = Literal["en", "ar"]
EntryId = Annotated[int, Path(gt=0, le=2_147_483_647)]
NOT_FOUND = "Help entry not found."
ANSWER_LIMIT = 4000


def _text(limit: int):
    def check(value: str) -> str:
        text = plain_text(value, limit)
        if not text:
            raise ValueError("Write some text first.")
        return text
    return AfterValidator(check)


class HelpEntryPublic(BaseModel):
    id: int
    question: str
    answer: str
    category: str
    language: Language


class HelpEntryAdmin(HelpEntryPublic):
    position: int
    published: bool
    updated_at: datetime
    updated_by_name: str | None


class HelpEntryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: Annotated[str, Field(min_length=1, max_length=300), _text(300)]
    answer: Annotated[str, Field(min_length=1, max_length=ANSWER_LIMIT), _text(ANSWER_LIMIT)]
    category: Annotated[str, Field(min_length=1, max_length=60), _text(60)]
    language: Language = "en"
    published: bool = False


class HelpOrder(BaseModel):
    model_config = ConfigDict(extra="forbid")
    language: Language
    ids: list[Annotated[int, Field(gt=0, le=2_147_483_647)]] = Field(min_length=1, max_length=500)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def ordered(language: Language | None = None, *, published_only: bool = False):
    query = select(FaqEntry)
    if language is not None:
        query = query.where(FaqEntry.language == language)
    if published_only:
        query = query.where(FaqEntry.published.is_(True))
    return query.order_by(FaqEntry.language, FaqEntry.position, FaqEntry.id)


def create_help_content_router(get_session) -> APIRouter:
    router = APIRouter(prefix="/help-content", tags=["Help content"],
                       dependencies=[Depends(require_feature("support_tools"))])

    @router.get("", response_model=list[HelpEntryPublic])
    def published(session: Annotated[Session, Depends(get_session)], response: Response, language: Language = "en"):
        """The published FAQ entries in one language, in order. No sign-in needed."""
        response.headers["Cache-Control"] = "no-cache"
        return [HelpEntryPublic.model_validate(row, from_attributes=True)
                for row in session.exec(ordered(language, published_only=True)).all()]

    return router


def create_help_content_admin_router(get_session, current_traveler) -> APIRouter:
    from .auth import check_origin

    admin = require_role(current_traveler, "admin")
    router = APIRouter(prefix="/admin/help-content", tags=["Help content"],
                       dependencies=[Depends(require_feature("support_tools")), Depends(admin)])
    SessionDep = Annotated[Session, Depends(get_session)]
    AdminDep = Annotated[Traveler, Depends(admin)]

    def entries(session: Session, rows: list[FaqEntry]) -> list[HelpEntryAdmin]:
        editors = sorted({row.updated_by for row in rows if row.updated_by})
        names = dict(session.exec(select(Traveler.id, Traveler.name).where(Traveler.id.in_(editors))).all()) if editors else {}
        return [HelpEntryAdmin(id=row.id, question=row.question, answer=row.answer, category=row.category,
                               language=row.language, position=row.position, published=row.published,
                               updated_at=_utc(row.updated_at),
                               updated_by_name=names.get(row.updated_by) if row.updated_by else None)
                for row in rows]

    def load(session: Session, entry_id: int) -> FaqEntry:
        row = session.get(FaqEntry, entry_id)
        if row is None:
            raise HTTPException(status_code=404, detail=NOT_FOUND)
        return row

    def next_position(session: Session, language: str) -> int:
        return (session.exec(select(func.max(FaqEntry.position)).where(FaqEntry.language == language)).one() or 0) + 1

    def audited(session: Session, request: Request, signed_in: Traveler, change: str) -> None:
        if record_activity(session, request.app.state.activity_directory, signed_in.id, "help_content", change,
                           "success") is None:
            raise HTTPException(status_code=401, detail="Your account is no longer available.")

    @router.get("", response_model=list[HelpEntryAdmin])
    def list_entries(session: SessionDep, language: Language | None = None):
        """Every entry, published or not, in order."""
        return entries(session, list(session.exec(ordered(language)).all()))

    @router.post("", response_model=HelpEntryAdmin, dependencies=[Depends(check_origin)])
    def add_entry(data: HelpEntryInput, request: Request, signed_in: AdminDep, session: SessionDep):
        row = FaqEntry(**data.model_dump(), position=next_position(session, data.language), updated_by=signed_in.id)
        session.add(row)
        audited(session, request, signed_in, "create")
        session.refresh(row)
        return entries(session, [row])[0]

    @router.put("/order", response_model=list[HelpEntryAdmin], dependencies=[Depends(check_origin)])
    def reorder(data: HelpOrder, request: Request, signed_in: AdminDep, session: SessionDep):
        """Put one language's entries in the order given. Every entry of that language must be listed once."""
        rows = {row.id: row for row in session.exec(ordered(data.language)).all()}
        if len(set(data.ids)) != len(data.ids) or set(data.ids) != set(rows):
            raise HTTPException(status_code=409, detail="The list changed. Reload it and try again.")
        now = datetime.now(timezone.utc)
        for position, entry_id in enumerate(data.ids, start=1):
            if rows[entry_id].position != position:
                rows[entry_id].position, rows[entry_id].updated_at = position, now
                rows[entry_id].updated_by = signed_in.id
                session.add(rows[entry_id])
        audited(session, request, signed_in, "reorder")
        return entries(session, list(session.exec(ordered(data.language)).all()))

    @router.put("/{entry_id}", response_model=HelpEntryAdmin, dependencies=[Depends(check_origin)])
    def edit_entry(entry_id: EntryId, data: HelpEntryInput, request: Request, signed_in: AdminDep, session: SessionDep):
        """Change an entry's text, category, language or whether it is published."""
        row = load(session, entry_id)
        if data.language != row.language:
            row.position = next_position(session, data.language)
        for field, value in data.model_dump().items():
            setattr(row, field, value)
        row.updated_by, row.updated_at = signed_in.id, datetime.now(timezone.utc)
        session.add(row)
        audited(session, request, signed_in, "update")
        session.refresh(row)
        return entries(session, [row])[0]

    @router.delete("/{entry_id}", status_code=204, response_class=Response, dependencies=[Depends(check_origin)])
    def delete_entry(entry_id: EntryId, request: Request, signed_in: AdminDep, session: SessionDep):
        session.delete(load(session, entry_id))
        audited(session, request, signed_in, "delete")
        return Response(status_code=204)

    return router
