"""Everything the service holds about one traveler (US 87), listed once.

`PERSONAL_DATA` names every table with rows about a traveler, how that traveler's rows are found,
and whether the data download includes them. Delete account (`privacy.complete_erasure`, CMPS271-117)
must remove every one of them: tests/test_data_export.py fills each table for one traveler, erases
the account and checks nothing is left, and fails when a new table refers to a traveler, an
application or a session without being listed here. Add a new table here, then to the erasure.

The download is one ZIP: `data.json` with every exported table, the passport images with their
confirmed fields, and a README. Secrets (password hash, session and reset token hashes) are never
exported. Another traveler's data never is either: document access rows by this account about
someone else's passport (staff work) are left out.
"""

import io
import json
import re
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response
from sqlmodel import Session, SQLModel, select

from .activity import record_activity
from .agent.storage import AgentFiles
from .models import (AccountErasure, ActivityEvent, AgentFieldEntry, AgentRun, AgentScreenshot, Application, ApplicationAnswer, AssistantMessage, AuthSession,
                     DocumentAccess, GoogleIdentity, Notification, PasswordResetChallenge, PasswordResetToken,
                     RefreshToken, SupportingDocument, Traveler, TravelerProfile)
from .documents.storage import DocumentStorage
from .security import throttle


@dataclass(frozen=True)
class Holding:
    model: type[SQLModel]
    # How this traveler's rows are found: by traveler id, through their applications, or their sessions.
    owner: str  # "traveler", "application", "session" or "run" (the form-filling agent's runs)
    exported: bool
    # Columns never exported because they are secrets, not information about the traveler.
    omit: tuple[str, ...] = ()
    # Why a table is left out of the download.
    reason: str = ""


PERSONAL_DATA: tuple[Holding, ...] = (
    Holding(Traveler, "traveler", True, omit=("password_hash",)),
    Holding(TravelerProfile, "traveler", True),
    Holding(GoogleIdentity, "traveler", True),
    Holding(AuthSession, "traveler", True, omit=("id", "refresh_hash")),
    Holding(Application, "traveler", True),
    Holding(ApplicationAnswer, "application", True),
    Holding(AssistantMessage, "traveler", True),
    Holding(Notification, "traveler", True),
    Holding(ActivityEvent, "traveler", True),
    Holding(DocumentAccess, "traveler", True, omit=("actor_id",)),
    Holding(SupportingDocument, "traveler", True),
    Holding(AgentRun, "traveler", True),
    Holding(AgentFieldEntry, "run", True),
    Holding(AgentScreenshot, "run", True),
    Holding(RefreshToken, "session", False, reason="Only hashes of sign-in tokens."),
    Holding(PasswordResetChallenge, "traveler", False, reason="Only a short-lived, hashed reset code."),
    Holding(PasswordResetToken, "traveler", False, reason="Only a short-lived, hashed reset token."),
    Holding(AccountErasure, "traveler", False, reason="Exists only while an account is being deleted."),
)


def owned_rows(session: Session, holding: Holding, traveler_id: int) -> list[SQLModel]:
    model = holding.model
    if model is Traveler:
        query = select(Traveler).where(Traveler.id == traveler_id)
    elif holding.owner == "application":
        query = select(model).where(model.application_id.in_(
            select(Application.id).where(Application.traveler_id == traveler_id)))
    elif holding.owner == "run":
        query = select(model).where(model.run_id.in_(
            select(AgentRun.id).where(AgentRun.traveler_id == traveler_id)))
    elif holding.owner == "session":
        query = select(model).where(model.session_id.in_(
            select(AuthSession.id).where(AuthSession.traveler_id == traveler_id)))
    else:
        # DocumentAccess: rows about this traveler's own passport only, never their staff work on others.
        query = select(model).where(model.traveler_id == traveler_id)
    return list(session.exec(query).all())


def _value(value: Any) -> Any:
    if isinstance(value, datetime):
        # SQLite drops timezone information when reading a datetime back.
        return (value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


# An admin's change to this account is logged as "admin:<staff account id>:<before>-><after>"
# (admin_api.apply_change). The download keeps the change but not which staff account made it.
STAFF_ID = re.compile(r"^admin:\d+:")


def _row(holding: Holding, row: SQLModel, traveler_id: int) -> dict:
    data = {column: _value(getattr(row, column)) for column in holding.model.model_fields if column not in holding.omit}
    if holding.model is DocumentAccess:
        data["accessed_by"] = "You" if row.actor_id == traveler_id else "Authorized staff"
    if holding.model is ActivityEvent:
        data["identifier"] = STAFF_ID.sub("admin:", data["identifier"])
    return data


README = """Your Ta'asheera data
==================

Downloaded {when} (UTC) for the account {email}.

data.json        Everything we hold about you, one section per kind of record:
                 your account, profile, linked Google account, sign-in sessions,
                 applications and their answers, supporting documents, what the
                 form-filling agent typed, assistant conversations, notifications,
                 account activity, and who opened your passport files.
passport/        Each passport photo you uploaded (.jpg) and the details you
                 confirmed from it (.json).
agent/           Each screenshot the form-filling agent took (.png), by application.
documents/       Each supporting document you uploaded (.pdf or .jpg), named by
                 its id in data.json's supportingdocument section.

Left out on purpose: your password (we only keep a hash of it), and the hashed
sign-in and reset tokens that keep your account secure. Nothing here belongs to
another traveler. To delete everything, use Delete account on your account page.
"""


def build_archive(session: Session, traveler: Traveler, passport_storage) -> bytes:
    now = datetime.now(timezone.utc)
    data: dict[str, Any] = {"exported_at": now.isoformat(), "traveler_id": traveler.id}
    for holding in PERSONAL_DATA:
        if holding.exported:
            data[holding.model.__tablename__] = [_row(holding, row, traveler.id)
                                                 for row in owned_rows(session, holding, traveler.id)]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("README.txt", README.format(when=now.strftime("%Y-%m-%d %H:%M"), email=traveler.email))
        archive.writestr("data.json", json.dumps(data, indent=2, ensure_ascii=False, default=str))
        folder = passport_storage.root / str(int(traveler.id))
        uploads = sorted(path.stem for path in folder.glob("*.jpg")) if folder.is_dir() else []
        for upload_id in uploads:
            # Through storage, so each file read is logged in the traveler's document access history.
            image = passport_storage.load(traveler.id, upload_id, purpose="data_export")
            if image is not None:
                archive.writestr(f"passport/{upload_id}.jpg", image)
            review = passport_storage.load_review(traveler.id, upload_id, purpose="data_export")
            if review is not None:
                archive.writestr(f"passport/{upload_id}.json", json.dumps(review, indent=2, ensure_ascii=False))
        documents = DocumentStorage(passport_storage.root, on_access=passport_storage.on_access)
        for document in session.exec(select(SupportingDocument).where(SupportingDocument.traveler_id == traveler.id)):
            extension = "pdf" if document.content_type == "application/pdf" else "jpg"
            content = documents.load(traveler.id, document.id, extension, purpose="data_export")
            if content is not None:
                archive.writestr(f"documents/{document.id}.{extension}", content)
        screenshots = AgentFiles(passport_storage.root, on_access=passport_storage.on_access)
        shots = session.exec(select(AgentScreenshot, AgentRun.application_id).join(AgentRun)
                             .where(AgentRun.traveler_id == traveler.id))
        for shot, application_id in shots:
            png = screenshots.load(traveler.id, application_id, shot.id, purpose="data_export")
            if png is not None:
                archive.writestr(f"agent/{application_id}/{shot.id}.png", png)
    return buffer.getvalue()


def create_data_export_router(get_session, current_traveler, passport_storage) -> APIRouter:
    router = APIRouter(prefix="/auth", tags=["Data download"])
    SessionDep = Annotated[Session, Depends(get_session)]
    TravelerDep = Annotated[Traveler, Depends(current_traveler)]

    @router.get("/account/export", response_class=Response,
                responses={200: {"content": {"application/zip": {}}, "description": "A ZIP of everything held"}})
    def export(request: Request, traveler: TravelerDep, session: SessionDep):
        """Download everything the service holds about the signed-in traveler, as one ZIP."""
        # A full export reads every file; a few per hour is plenty for a person.
        throttle(request, session, "data-export", account=str(traveler.id), ip_limit=20, account_limit=5)
        archive = build_archive(session, traveler, passport_storage)
        record_activity(session, request.app.state.activity_directory, traveler.id,
                        "data_export", "account", "success")
        session.commit()
        name = f"taasheera-data-{datetime.now(timezone.utc).date().isoformat()}.zip"
        return Response(archive, media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})

    return router

