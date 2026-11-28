import re
from collections.abc import Callable
from datetime import date, datetime, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session, delete, select

from ..applications import completeness, flow
from ..applications.passport_rules import RuleSummary, passport_issues, summary
from ..assistant import Ask, AssistantUnavailable
from ..features import require_feature
from ..models import Application, ApplicationAnswer, PassportPages, SupportingDocument, Traveler
from ..security import admitted, audit, client_address
from .photo import photo_issues
from .router import MAX_PER_APPLICATION, MAX_PER_REQUIREMENT, owned_documents
from .storage import DocumentStorage
from .translation import LANGUAGES, NOTICE, TranslationRefused, embassy_language, translation_request
from .usage import FILE_KEY, rows_for_file

MIN_TRANSLATION_TEXT = 20
Language = Literal[tuple(LANGUAGES)]


class VaultUse(BaseModel):
    id: str
    application_id: str
    application_title: str
    place: str | None
    requirement: str
    requirement_label: str
    archived: bool


class LanguageOption(BaseModel):
    code: str
    label: str


class VaultDocument(BaseModel):
    id: str
    label: str
    filename: str
    content_type: str
    size_bytes: int
    uploaded_at: datetime
    uses: list[VaultUse]
    languages: list[LanguageOption]


class RequirementOption(BaseModel):
    id: str
    label: str


class AttachTarget(BaseModel):
    application_id: str
    title: str
    place: str | None
    requirements: list[RequirementOption]


class Vault(BaseModel):
    documents: list[VaultDocument]
    targets: list[AttachTarget]


class AttachRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    application_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    requirement: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=40)


class TranslationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    language: Language = "en"


class TranslationPublic(BaseModel):
    document_id: str
    language: str
    language_label: str
    text: str
    truncated: bool
    notice: str


class PassportPagesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    blank_pages: int = Field(ge=0, le=100)


class PassportCheck(BaseModel):
    blank_pages: int | None
    rules: list[RuleSummary]


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def declared_pages(session: Session, traveler_id: int) -> int | None:
    row = session.get(PassportPages, traveler_id)
    return row.blank_pages if row else None


def stored_answers(session: Session, application_ids: list[str]) -> dict[str, flow.Answers]:
    answers: dict[str, flow.Answers] = {application_id: {} for application_id in application_ids}
    if answers:
        for row in session.exec(select(ApplicationAnswer).where(ApplicationAnswer.application_id.in_(list(answers)))):
            answers[row.application_id][row.question_id] = row.value
    return answers


def vault_issues(session: Session, storage: DocumentStorage, application: Application, answers: flow.Answers, *,
                 photos: bool, passport: bool, today: date | None = None) -> list[completeness.Issue]:
    from ..applications.router import check_day

    issues: list[completeness.Issue] = []
    if photos:
        issues += photo_issues(storage, application, answers, owned_documents(session, application.id))
    if passport:
        issues += passport_issues(flow.load_questionnaire(application.questionnaire_id), answers,
                                  check_day(application, today or date.today()),
                                  blank_pages=declared_pages(session, application.traveler_id))
    return issues


def build_vault(session: Session, traveler_id: int) -> Vault:
    applications = {application.id: application for application in session.exec(
        select(Application).where(Application.traveler_id == traveler_id).order_by(Application.updated_at.desc()))}
    answers = stored_answers(session, list(applications))
    questionnaires = {key: flow.load_questionnaire(application.questionnaire_id)
                      for key, application in applications.items()}
    places = {key: flow.summary_place(questionnaire, answers[key]) for key, questionnaire in questionnaires.items()}
    grouped: dict[str, list[SupportingDocument]] = {}
    for row in session.exec(select(SupportingDocument).where(SupportingDocument.traveler_id == traveler_id)
                            .order_by(SupportingDocument.uploaded_at, SupportingDocument.id)):
        grouped.setdefault(row.file_key, []).append(row)
    documents = []
    for file_key, rows in grouped.items():
        rows.sort(key=lambda row: (row.id != file_key, _utc(applications[row.application_id].created_at), row.id))
        uses = []
        codes = ["en"]
        for row in rows:
            questionnaire = questionnaires[row.application_id]
            labels = {document.id: document.label for document in questionnaire.documents}
            uses.append(VaultUse(id=row.id, application_id=row.application_id, application_title=questionnaire.title,
                                 place=places[row.application_id], requirement=row.requirement,
                                 requirement_label=labels.get(row.requirement, "Document"),
                                 archived=applications[row.application_id].archived_at is not None))
            code = embassy_language(questionnaire.id, answers[row.application_id])
            if code and code not in codes:
                codes.append(code)
        first = rows[0]
        documents.append(VaultDocument(
            id=file_key, label=uses[0].requirement_label, filename=first.filename, content_type=first.content_type,
            size_bytes=first.size_bytes, uploaded_at=_utc(first.uploaded_at), uses=uses,
            languages=[LanguageOption(code=code, label=LANGUAGES[code]) for code in codes]))
    documents.sort(key=lambda document: document.uploaded_at, reverse=True)
    targets = [AttachTarget(application_id=key, title=questionnaires[key].title, place=places[key],
                            requirements=[RequirementOption(id=requirement.id, label=requirement.label)
                                          for requirement in flow.required_documents(questionnaires[key], answers[key])])
               for key, application in applications.items() if application.archived_at is None]
    return Vault(documents=documents, targets=targets)


def passport_check(session: Session, traveler_id: int) -> PassportCheck:
    questionnaire_ids = session.exec(select(Application.questionnaire_id).where(
        Application.traveler_id == traveler_id, Application.archived_at.is_(None)).order_by(Application.updated_at.desc()))
    seen = list(dict.fromkeys(questionnaire_ids))
    return PassportCheck(blank_pages=declared_pages(session, traveler_id),
                         rules=[summary(flow.load_questionnaire(questionnaire_id)) for questionnaire_id in seen])


def create_vault_router(
    get_session,
    current_traveler,
    storage: DocumentStorage,
    *,
    record_event: Callable[[int, str, str], None] | None = None,
    translate: Ask | None = None,
) -> APIRouter:
    from ..auth import check_origin

    router = APIRouter(prefix="/vault", tags=["Document vault"])
    SessionDep = Annotated[Session, Depends(get_session)]
    TravelerDep = Annotated[Traveler, Depends(current_traveler)]
    vault_on = [Depends(require_feature("supporting_documents")), Depends(require_feature("document_vault"))]
    changes = [*vault_on, Depends(check_origin)]
    passport_on = [Depends(require_feature("passport_rules"))]

    def event(traveler_id: int, event_type: str, requirement: str) -> None:
        if record_event:
            record_event(traveler_id, event_type, requirement)

    def file_rows(session: Session, traveler: Traveler, document_id: str) -> list[SupportingDocument]:
        rows = rows_for_file(session, traveler.id, document_id) if re.fullmatch(r"[0-9a-f]{32}", document_id) else []
        if not rows:
            raise HTTPException(status_code=404, detail="Document not found.")
        return rows

    @router.get("/documents", response_model=Vault, dependencies=vault_on)
    def list_vault(traveler: TravelerDep, session: SessionDep, response: Response):
        response.headers["Cache-Control"] = "private, no-store"
        return build_vault(session, traveler.id)

    @router.post("/documents/{document_id}/attachments", response_model=VaultUse, status_code=201,
                 dependencies=changes)
    def attach(document_id: str, data: AttachRequest, traveler: TravelerDep, session: SessionDep, response: Response):
        rows = file_rows(session, traveler, document_id)
        application = session.get(Application, data.application_id)
        if application is None or application.traveler_id != traveler.id:
            raise HTTPException(status_code=404, detail="Application not found.")
        if application.archived_at is not None:
            raise HTTPException(status_code=409,
                                detail="This application is archived. Restore it from your dashboard first.")
        questionnaire = flow.load_questionnaire(application.questionnaire_id)
        answers = stored_answers(session, [application.id])[application.id]
        requirements = {requirement.id: requirement for requirement in flow.required_documents(questionnaire, answers)}
        if data.requirement not in requirements:
            raise HTTPException(status_code=422, detail="This application doesn't ask for that document.")
        if any(row.application_id == application.id and row.requirement == data.requirement for row in rows):
            raise HTTPException(status_code=409, detail="This file is already attached to that document.")
        documents = owned_documents(session, application.id)
        if len(documents) >= MAX_PER_APPLICATION:
            raise HTTPException(status_code=409, detail="This application has as many files as it can hold.")
        if sum(d.requirement == data.requirement for d in documents) >= MAX_PER_REQUIREMENT:
            raise HTTPException(status_code=409, detail=(
                f"You can upload up to {MAX_PER_REQUIREMENT} files for one document. Delete one first."))
        source = rows[0]
        document = SupportingDocument(
            id=storage.new_id(), file_id=document_id, traveler_id=traveler.id, application_id=application.id,
            requirement=data.requirement, filename=source.filename, content_type=source.content_type,
            size_bytes=source.size_bytes, uploaded_at=source.uploaded_at)
        session.add(document)
        session.commit()
        event(traveler.id, "document_attach", data.requirement)
        response.headers["Cache-Control"] = "private, no-store"
        return VaultUse(id=document.id, application_id=application.id, application_title=questionnaire.title,
                        place=flow.summary_place(questionnaire, answers), requirement=data.requirement,
                        requirement_label=requirements[data.requirement].label, archived=False)

    @router.delete("/documents/{document_id}", status_code=204, dependencies=changes)
    def remove(document_id: str, traveler: TravelerDep, session: SessionDep):
        rows = file_rows(session, traveler, document_id)
        if storage.on_access is not None:
            storage.on_access(traveler.id, document_id, "document_delete")
        session.exec(delete(SupportingDocument).where(SupportingDocument.traveler_id == traveler.id,
                                                      FILE_KEY == document_id))
        session.commit()
        storage.delete(traveler.id, document_id)
        event(traveler.id, "document_vault_delete", rows[0].requirement)
        return Response(status_code=204, headers={"Cache-Control": "private, no-store"})

    @router.post("/documents/{document_id}/translation", response_model=TranslationPublic,
                 dependencies=[*changes, Depends(require_feature("document_translation"))])
    def translate_document(document_id: str, data: TranslationRequest, request: Request, traveler: TravelerDep,
                           session: SessionDep, response: Response):
        if translate is None:
            raise HTTPException(status_code=503, detail="Translation isn't set up on this server yet.")
        rows = file_rows(session, traveler, document_id)
        if not admitted(request, session, [
            ("translation-account", str(traveler.id), 10, 600),
            ("translation-ip", client_address(request), 30, 600),
        ]):
            audit(request, "translation", "throttled", traveler.id)
            raise HTTPException(status_code=429, detail="You're translating quickly. Wait a few minutes and try again.")
        text = storage.load_text(traveler.id, document_id, purpose="document_translation")
        if len(text.strip()) < MIN_TRANSLATION_TEXT:
            raise HTTPException(status_code=422, detail=(
                "We couldn't read any text in this document to translate. A clear scan or a PDF with selectable "
                "text works best."))
        system, messages, truncated = translation_request(text, data.language)
        try:
            translated = translate(system, messages)
        except AssistantUnavailable:
            event(traveler.id, "document_translate_unavailable", rows[0].requirement)
            raise HTTPException(status_code=503,
                                detail="Translation isn't available right now. Please try again in a moment.") from None
        except TranslationRefused:
            raise HTTPException(status_code=422, detail="This document couldn't be translated.") from None
        event(traveler.id, "document_translate", rows[0].requirement)
        response.headers["Cache-Control"] = "private, no-store"
        return TranslationPublic(document_id=document_id, language=data.language,
                                 language_label=LANGUAGES[data.language], text=translated, truncated=truncated,
                                 notice=NOTICE)

    @router.get("/passport", response_model=PassportCheck, dependencies=passport_on)
    def get_passport(traveler: TravelerDep, session: SessionDep, response: Response):
        response.headers["Cache-Control"] = "private, no-store"
        return passport_check(session, traveler.id)

    @router.put("/passport", response_model=PassportCheck, dependencies=[*passport_on, Depends(check_origin)])
    def put_passport(data: PassportPagesRequest, traveler: TravelerDep, session: SessionDep, response: Response):
        row = session.get(PassportPages, traveler.id) or PassportPages(traveler_id=traveler.id, blank_pages=0)
        row.blank_pages, row.updated_at = data.blank_pages, datetime.now(timezone.utc)
        session.add(row)
        session.commit()
        event(traveler.id, "passport_pages", "passport")
        response.headers["Cache-Control"] = "private, no-store"
        return passport_check(session, traveler.id)

    return router
