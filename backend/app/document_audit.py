"""Audit at the storage boundary, before passport content leaves private storage."""
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session, func, select

from .activity import record_activity
from .models import DocumentAccess, Traveler

PURPOSES = frozenset({"image_view", "extraction", "review_read", "review_update", "assistant_context",
                      "application_prefill", "data_export",
                      # Supporting documents (CMPS271-29): opened by the traveler, read by the cross-check.
                      "document_view", "document_check", "document_packet", "document_prefill",
                      "document_translation", "document_delete",
                      # The passport read by the document cross-check (CMPS271-50).
                      "consistency_check",
                      # A screenshot the form-filling agent took of a filled page (CMPS271-70).
                      "agent_screenshot"})


def record_document_access(engine, directory, owner_id: int, upload_id: str, purpose: str):
    if purpose not in PURPOSES:
        raise ValueError("Unknown passport access purpose")
    category = ("supporting_document" if purpose.startswith("document_")
                else "agent" if purpose.startswith("agent_") else "passport")
    with Session(engine) as session:
        session.add(DocumentAccess(traveler_id=owner_id, actor_id=owner_id,
                                   upload_id=upload_id, purpose=purpose))
        # Commits both entries. Failure deliberately blocks access to the content.
        if record_activity(session, directory, owner_id, "document_access", category, "success") is None:
            raise HTTPException(401, "Passport owner is no longer available.")


def accessor(row: DocumentAccess, traveler_id: int) -> str:
    """Who read it, as the traveler sees it: staff are never named (CMPS271-128)."""
    if row.actor_id == traveler_id:
        return "You"
    return "Support" if row.purpose == "support_view" else "Authorized staff"


class AccessPublic(BaseModel):
    id: int
    upload_id: str
    actor_id: int | None
    accessor: str
    purpose: str
    occurred_at: datetime


class AccessPage(BaseModel):
    items: list[AccessPublic]
    total: int
    page: int
    page_size: int


class AccessSearch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page: int = Field(default=1, ge=1, le=100000)
    page_size: int = Field(default=20, ge=1, le=100)


def create_document_audit_router(get_session, current_traveler):
    router = APIRouter(prefix="/activity", tags=["Document access"])

    @router.get("/documents", response_model=AccessPage)
    def document_history(search: Annotated[AccessSearch, Query()], response: Response,
                         session: Annotated[Session, Depends(get_session)],
                         traveler: Annotated[Traveler, Depends(current_traveler)]):
        condition = DocumentAccess.traveler_id == traveler.id
        rows = session.exec(select(DocumentAccess).where(condition)
            .order_by(DocumentAccess.occurred_at.desc(), DocumentAccess.id.desc())
            .offset((search.page - 1) * search.page_size).limit(search.page_size)).all()
        response.headers["Cache-Control"] = "no-store"
        return AccessPage(items=[AccessPublic(id=row.id, upload_id=row.upload_id,
            actor_id=row.actor_id if row.actor_id == traveler.id else None, accessor=accessor(row, traveler.id),
            purpose=row.purpose, occurred_at=row.occurred_at) for row in rows],
            total=session.exec(select(func.count()).select_from(DocumentAccess).where(condition)).one(),
            page=search.page, page_size=search.page_size)

    return router
