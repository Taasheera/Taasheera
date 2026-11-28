"""Owner-only routes for generated documents (CMPS271-57/58/59/60/61/62).

`/applications/{id}/generated` lists what this application can have; each document reads as the traveler's
saved edit when there is one, or is written fresh from the current answers. PUT saves an edit, DELETE throws
it away (regenerate), and `/pdf` prints it. Archived applications can be read and printed but not edited.
"""
from datetime import date, datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session, select

from ..applications import flow
from ..applications.router import _owned_answers, current_answers
from ..auth import check_origin
from ..features import require_feature
from ..models import Application, GeneratedDocument, Traveler
from .pdf import render_pdf
from .templates import KINDS, LANGUAGES, TRANSLATED, available_kinds, embassy_languages, facts, generate

NOTICE = ("Written from your own answers. Read it, correct anything that isn't right, and sign it yourself. "
          "Ta'asheera doesn't give legal or immigration advice.")
PRIVATE = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}


class LanguageOption(BaseModel):
    language: str
    label: str
    edited: bool


class KindOption(BaseModel):
    kind: str
    label: str
    languages: list[LanguageOption]


class Overview(BaseModel):
    documents: list[KindOption]
    read_only: bool
    notice: str


class DocumentView(BaseModel):
    kind: str
    language: str
    title: str
    body: str
    # The traveler saved their own version; `outdated` when answers changed after that.
    edited: bool
    outdated: bool
    saved_at: datetime | None
    read_only: bool
    notice: str


class EditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: str = Field(min_length=1, max_length=20000)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _saved(session: Session, application_id: str, kind: str, language: str) -> GeneratedDocument | None:
    return session.exec(select(GeneratedDocument).where(
        GeneratedDocument.application_id == application_id, GeneratedDocument.kind == kind,
        GeneratedDocument.language == language)).first()


def _languages(kind: str, questionnaire: flow.Questionnaire, answers: flow.Answers) -> list[str]:
    return embassy_languages(questionnaire, answers) if kind in TRANSLATED else ["en"]


def generated_text(session: Session, application: Application, questionnaire: flow.Questionnaire,
                   answers: flow.Answers, email: str, kind: str, language: str = "en",
                   today: date | None = None) -> tuple[str, str]:
    """(title, body): the traveler's saved edit, or the document written from the answers."""
    written = generate(kind, language, facts(questionnaire, answers, email), today or date.today())
    saved = _saved(session, application.id, kind, language)
    return written.title, saved.body if saved else written.body


def generated_packet_files(session: Session, application: Application, questionnaire: flow.Questionnaire,
                           answers: flow.Answers, email: str) -> dict[str, tuple[str, str, bytes]]:
    """PDFs of the cover letter and itinerary, keyed by the checklist item they stand in for in the packet."""
    files = {}
    for kind in ("cover_letter", "itinerary"):
        title, body = generated_text(session, application, questionnaire, answers, email, kind)
        files[kind] = (f"{title} (prepared with Ta'asheera).pdf", "application/pdf", render_pdf(title, body))
    return files


def create_generated_documents_router(get_session, current_traveler) -> APIRouter:
    router = APIRouter(prefix="/applications/{application_id}/generated", tags=["Generated documents"],
                       dependencies=[Depends(require_feature("generated_documents"))])
    SessionDep = Annotated[Session, Depends(get_session)]
    TravelerDep = Annotated[Traveler, Depends(current_traveler)]

    def load(session: Session, traveler: Traveler, application_id: str):
        owned = _owned_answers(session, traveler.id, application_id)
        if owned is None:
            raise HTTPException(status_code=404, detail="Application not found.")
        application, questionnaire, stored = owned
        answers, _ = current_answers(questionnaire, application, stored, date.today())
        return application, questionnaire, answers

    def checked(questionnaire: flow.Questionnaire, answers: flow.Answers, kind: str, language: str) -> None:
        if kind not in available_kinds(questionnaire, answers):
            raise HTTPException(status_code=404, detail="This application has no such document.")
        if language not in _languages(kind, questionnaire, answers):
            raise HTTPException(status_code=404, detail="This document isn't available in that language.")

    def view(session: Session, traveler: Traveler, application: Application, questionnaire: flow.Questionnaire,
             answers: flow.Answers, kind: str, language: str) -> DocumentView:
        written = generate(kind, language, facts(questionnaire, answers, traveler.email), date.today())
        saved = _saved(session, application.id, kind, language)
        return DocumentView(
            kind=kind, language=language, title=written.title, body=saved.body if saved else written.body,
            edited=saved is not None,
            outdated=saved is not None and _utc(application.updated_at) > _utc(saved.updated_at),
            saved_at=_utc(saved.updated_at) if saved else None,
            read_only=application.archived_at is not None, notice=NOTICE)

    def writable(application: Application) -> None:
        if application.archived_at is not None:
            raise HTTPException(status_code=409, detail="This application is archived. Restore it to edit its documents.")

    LanguageQuery = Annotated[str, Query(pattern=r"^[a-z]{2}$")]

    @router.get("", response_model=Overview)
    def overview(application_id: str, traveler: TravelerDep, session: SessionDep, response: Response):
        response.headers.update(PRIVATE)
        application, questionnaire, answers = load(session, traveler, application_id)
        edited = {(row.kind, row.language) for row in session.exec(
            select(GeneratedDocument).where(GeneratedDocument.application_id == application.id))}
        documents = [KindOption(kind=kind, label=KINDS[kind], languages=[
            LanguageOption(language=language, label=LANGUAGES[language], edited=(kind, language) in edited)
            for language in _languages(kind, questionnaire, answers)]) for kind in available_kinds(questionnaire, answers)]
        return Overview(documents=documents, read_only=application.archived_at is not None, notice=NOTICE)

    @router.get("/{kind}", response_model=DocumentView)
    def read(application_id: str, kind: str, traveler: TravelerDep, session: SessionDep, response: Response,
             language: LanguageQuery = "en"):
        response.headers.update(PRIVATE)
        application, questionnaire, answers = load(session, traveler, application_id)
        checked(questionnaire, answers, kind, language)
        return view(session, traveler, application, questionnaire, answers, kind, language)

    @router.put("/{kind}", response_model=DocumentView, dependencies=[Depends(check_origin)])
    def save(application_id: str, kind: str, request: EditRequest, traveler: TravelerDep, session: SessionDep,
             response: Response, language: LanguageQuery = "en"):
        response.headers.update(PRIVATE)
        application, questionnaire, answers = load(session, traveler, application_id)
        checked(questionnaire, answers, kind, language)
        writable(application)
        saved = _saved(session, application.id, kind, language)
        now = datetime.now(timezone.utc)
        if saved is None:
            saved = GeneratedDocument(traveler_id=traveler.id, application_id=application.id, kind=kind,
                                      language=language, body=request.body, updated_at=now)
        else:
            saved.body, saved.updated_at = request.body, now
        session.add(saved)
        session.commit()
        return view(session, traveler, application, questionnaire, answers, kind, language)

    @router.delete("/{kind}", response_model=DocumentView, dependencies=[Depends(check_origin)])
    def regenerate(application_id: str, kind: str, traveler: TravelerDep, session: SessionDep, response: Response,
                   language: LanguageQuery = "en"):
        """Forget the traveler's edit, so the document is written again from the current answers."""
        response.headers.update(PRIVATE)
        application, questionnaire, answers = load(session, traveler, application_id)
        checked(questionnaire, answers, kind, language)
        writable(application)
        saved = _saved(session, application.id, kind, language)
        if saved is not None:
            session.delete(saved)
            session.commit()
        return view(session, traveler, application, questionnaire, answers, kind, language)

    @router.get("/{kind}/pdf")
    def pdf(application_id: str, kind: str, traveler: TravelerDep, session: SessionDep, language: LanguageQuery = "en"):
        application, questionnaire, answers = load(session, traveler, application_id)
        checked(questionnaire, answers, kind, language)
        title, body = generated_text(session, application, questionnaire, answers, traveler.email, kind, language)
        filename = f"{kind.replace('_', '-')}-{language}.pdf"
        return Response(render_pdf(title, body), media_type="application/pdf", headers={
            **PRIVATE, "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Security-Policy": "sandbox; default-src 'none'"})

    return router
