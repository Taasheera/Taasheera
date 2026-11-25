"""Upload, list, replace and delete an application's supporting documents (CMPS271-29).

Routes sit under /applications/{application_id}/documents, so they share the applications
proxy path. Like every application route, another traveler's application or document is a
404, the same as one that doesn't exist. Archived applications are read-only (US 79).
Hidden behind the `supporting_documents` feature flag until it passes testing.
"""

import re
import unicodedata
from collections.abc import Callable
from datetime import date, datetime
from pathlib import PurePath
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel
from sqlmodel import Session, select

from ..applications import flow
from ..features import require_feature
from ..models import Application, ApplicationAnswer, SupportingDocument, Traveler
from ..passport.images import MAX_UPLOAD_BYTES
from .crosscheck import Report as CrossCheckReport
from .files import InvalidDocumentError, check_document, read_text
from .packet import PacketItem, build_packet
from .review import document_cross_check
from .storage import DocumentStorage

# Enough for a few months of statements per requirement; stops one application filling the disk.
MAX_PER_REQUIREMENT = 5
MAX_PER_APPLICATION = 40


class DocumentPublic(BaseModel):
    id: str
    requirement: str
    filename: str
    content_type: str
    size_bytes: int
    uploaded_at: datetime


class RequirementPublic(BaseModel):
    id: str
    label: str
    help: str | None
    required: bool
    documents: list[DocumentPublic]


class Checklist(BaseModel):
    requirements: list[RequirementPublic]
    # Files uploaded for a document that the current answers no longer ask for.
    other: list[DocumentPublic]
    # Every required document has at least one file.
    complete: bool
    read_only: bool


def clean_filename(name: str | None, extension: str) -> str:
    """The traveler's own file name, safe to show: no folders, control characters or odd lengths.

    Display only: storage paths use our random ids. The extension is set from the checked content.
    """
    stem = PurePath((name or "").replace("\\", "/")).stem
    stem = re.sub(r"\s+", " ", unicodedata.normalize("NFC", stem))
    stem = "".join(ch for ch in stem if unicodedata.category(ch)[0] != "C").strip(" .")[:100] or "document"
    return f"{stem}.{extension}"


def public(document: SupportingDocument) -> DocumentPublic:
    return DocumentPublic(id=document.id, requirement=document.requirement, filename=document.filename,
                          content_type=document.content_type, size_bytes=document.size_bytes,
                          uploaded_at=document.uploaded_at)


def owned_documents(session: Session, application_id: str) -> list[SupportingDocument]:
    return list(session.exec(select(SupportingDocument)
                             .where(SupportingDocument.application_id == application_id)
                             .order_by(SupportingDocument.uploaded_at, SupportingDocument.id)).all())


def create_documents_router(
    get_session,
    current_traveler,
    storage: DocumentStorage,
    *,
    record_event: Callable[[int, str, str], None] | None = None,
    reader: Callable = read_text,
    load_latest_passport: Callable[[int], dict | None] = lambda traveler_id: None,
    upload_dependencies: list | None = None,
) -> APIRouter:
    from ..applications.router import saved_profile
    from ..auth import check_origin

    router = APIRouter(prefix="/applications/{application_id}/documents", tags=["Supporting documents"],
                       dependencies=[Depends(require_feature("supporting_documents"))])
    SessionDep = Annotated[Session, Depends(get_session)]
    TravelerDep = Annotated[Traveler, Depends(current_traveler)]
    # Every route that accepts a new file also runs these, such as privacy policy acceptance (CMPS271-115).
    upload_checks = [Depends(check_origin), *(upload_dependencies or [])]

    def owned(session: Session, traveler: Traveler, application_id: str) -> Application:
        application = session.get(Application, application_id)
        if application is None or application.traveler_id != traveler.id:
            raise HTTPException(status_code=404, detail="Application not found.")
        return application

    def writable(session: Session, traveler: Traveler, application_id: str) -> Application:
        application = owned(session, traveler, application_id)
        if application.archived_at is not None:
            raise HTTPException(status_code=409,
                                detail="This application is archived. Restore it from your dashboard first.")
        return application

    def owned_document(session: Session, application: Application, document_id: str) -> SupportingDocument:
        document = session.get(SupportingDocument, document_id)
        if document is None or document.application_id != application.id:
            raise HTTPException(status_code=404, detail="Document not found.")
        return document

    def answers(session: Session, application: Application) -> flow.Answers:
        rows = session.exec(select(ApplicationAnswer).where(ApplicationAnswer.application_id == application.id))
        return {row.question_id: row.value for row in rows}

    def checklist(session: Session, application: Application) -> Checklist:
        questionnaire = flow.load_questionnaire(application.questionnaire_id)
        requirements = flow.required_documents(questionnaire, answers(session, application))
        documents = owned_documents(session, application.id)
        by_requirement: dict[str, list[DocumentPublic]] = {}
        for document in documents:
            by_requirement.setdefault(document.requirement, []).append(public(document))
        listed = {requirement.id for requirement in requirements}
        return Checklist(
            requirements=[RequirementPublic(id=r.id, label=r.label, help=r.help, required=r.required,
                                            documents=by_requirement.get(r.id, [])) for r in requirements],
            other=[public(d) for d in documents if d.requirement not in listed],
            complete=all(by_requirement.get(r.id) for r in requirements if r.required),
            read_only=application.archived_at is not None,
        )

    def read_upload(file: UploadFile):
        # One byte past the limit, so an oversized file is caught without reading it all.
        try:
            return check_document(file.file.read(MAX_UPLOAD_BYTES + 1))
        except InvalidDocumentError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from None

    def event(traveler_id: int, event_type: str, requirement: str) -> None:
        if record_event:
            # The requirement id comes from our questionnaire file, never from the file itself.
            record_event(traveler_id, event_type, requirement)

    @router.get("", response_model=Checklist)
    def list_documents(application_id: str, traveler: TravelerDep, session: SessionDep, response: Response):
        response.headers["Cache-Control"] = "private, no-store"
        return checklist(session, owned(session, traveler, application_id))

    @router.get("/packet")
    def packet(application_id: str, traveler: TravelerDep, session: SessionDep):
        """One printable PDF: a cover checklist, then every uploaded document in the checklist's order."""
        application = owned(session, traveler, application_id)
        questionnaire = flow.load_questionnaire(application.questionnaire_id)
        given = answers(session, application)
        requirements = flow.required_documents(questionnaire, given)
        documents = owned_documents(session, application.id)
        items = []
        for requirement in requirements:
            files = []
            for document in (d for d in documents if d.requirement == requirement.id):
                extension = "pdf" if document.content_type == "application/pdf" else "jpg"
                content = storage.load(traveler.id, document.id, extension, purpose="document_packet")
                if content is not None:
                    files.append((document.filename, document.content_type, content))
            items.append(PacketItem(label=requirement.label, required=requirement.required, files=files))
        passport = given.get("passport") if isinstance(given.get("passport"), dict) else {}
        name = " ".join(part for part in (passport.get("given_names"), passport.get("surname")) if part)
        dates = [given.get(key) for key in ("arrival_date", "departure_date") if given.get(key)]
        trip = " to ".join(date.fromisoformat(value).strftime("%d %B %Y") for value in dates)
        content = build_packet(questionnaire.title, name, f"Trip: {trip}" if trip else "", items)
        return Response(content, media_type="application/pdf", headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": 'attachment; filename="application-packet.pdf"',
            "Content-Security-Policy": "sandbox; default-src 'none'",
            "X-Content-Type-Options": "nosniff",
        })

    @router.get("/crosscheck", response_model=CrossCheckReport)
    def crosscheck(application_id: str, traveler: TravelerDep, session: SessionDep, response: Response):
        """Where the name, date of birth or passport number disagrees with the passport (CMPS271-50)."""
        application = owned(session, traveler, application_id)
        questionnaire = flow.load_questionnaire(application.questionnaire_id)
        given, _ = flow.reconcile(questionnaire, answers(session, application), date.today())
        response.headers["Cache-Control"] = "private, no-store"
        return document_cross_check(session, storage, application, given,
                                    latest_passport=load_latest_passport(traveler.id),
                                    profile=saved_profile(session, traveler.id))

    @router.post("", response_model=DocumentPublic, status_code=201, dependencies=upload_checks)
    def upload(application_id: str, requirement: Annotated[str, Form(max_length=40)], file: UploadFile,
               traveler: TravelerDep, session: SessionDep, response: Response):
        application = writable(session, traveler, application_id)
        questionnaire = flow.load_questionnaire(application.questionnaire_id)
        if requirement not in {r.id for r in flow.required_documents(questionnaire, answers(session, application))}:
            raise HTTPException(status_code=422, detail="This application doesn't ask for that document.")
        documents = owned_documents(session, application.id)
        if len(documents) >= MAX_PER_APPLICATION:
            raise HTTPException(status_code=409, detail="This application has as many files as it can hold.")
        if sum(d.requirement == requirement for d in documents) >= MAX_PER_REQUIREMENT:
            raise HTTPException(status_code=409, detail=(
                f"You can upload up to {MAX_PER_REQUIREMENT} files for one document. Delete one first, or combine them into one PDF."))
        checked = read_upload(file)
        document = SupportingDocument(
            id=storage.new_id(), traveler_id=traveler.id, application_id=application.id, requirement=requirement,
            filename=clean_filename(file.filename, checked.extension), content_type=checked.content_type,
            size_bytes=len(checked.content))
        storage.save(traveler.id, document.id, checked.extension, checked.content, reader(checked))
        session.add(document)
        try:
            session.commit()
        except Exception:
            session.rollback()
            storage.delete(traveler.id, document.id)
            raise
        event(traveler.id, "document_upload", requirement)
        response.headers["Cache-Control"] = "private, no-store"
        return public(document)

    @router.put("/{document_id}", response_model=DocumentPublic, dependencies=upload_checks)
    def replace(application_id: str, document_id: str, file: UploadFile,
                traveler: TravelerDep, session: SessionDep, response: Response):
        """A new file for the same requirement. It gets a new id, so a cached old file is never shown as the new one."""
        application = writable(session, traveler, application_id)
        old = owned_document(session, application, document_id)
        checked = read_upload(file)
        new = SupportingDocument(
            id=storage.new_id(), traveler_id=traveler.id, application_id=application.id, requirement=old.requirement,
            filename=clean_filename(file.filename, checked.extension), content_type=checked.content_type,
            size_bytes=len(checked.content))
        storage.save(traveler.id, new.id, checked.extension, checked.content, reader(checked))
        session.delete(old)
        session.add(new)
        try:
            session.commit()
        except Exception:
            session.rollback()
            storage.delete(traveler.id, new.id)
            raise
        # Only once the new row is saved: until then the old file is still the one on record.
        storage.delete(traveler.id, document_id)
        event(traveler.id, "document_replace", new.requirement)
        response.headers["Cache-Control"] = "private, no-store"
        return public(new)

    @router.delete("/{document_id}", status_code=204, dependencies=[Depends(check_origin)])
    def remove(application_id: str, document_id: str, traveler: TravelerDep, session: SessionDep):
        application = writable(session, traveler, application_id)
        document = owned_document(session, application, document_id)
        requirement = document.requirement
        session.delete(document)
        session.commit()
        storage.delete(traveler.id, document_id)
        event(traveler.id, "document_delete", requirement)
        return Response(status_code=204, headers={"Cache-Control": "private, no-store"})

    @router.get("/{document_id}/file")
    def download(application_id: str, document_id: str, traveler: TravelerDep, session: SessionDep):
        document = owned_document(session, owned(session, traveler, application_id), document_id)
        extension = "pdf" if document.content_type == "application/pdf" else "jpg"
        content = storage.load(traveler.id, document.id, extension, purpose="document_view")
        if content is None:
            raise HTTPException(status_code=404, detail="Document not found.")
        # PDFs download rather than open on our origin; images are our own re-encoded JPEGs.
        disposition = "attachment" if extension == "pdf" else "inline"
        ascii_name = document.filename.encode("ascii", "replace").decode().replace('"', "'").replace("?", "_")
        return Response(content, media_type=document.content_type, headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": f'{disposition}; filename="{ascii_name}"',
            "Content-Security-Policy": "sandbox; default-src 'none'",
            "X-Content-Type-Options": "nosniff",
        })

    return router
