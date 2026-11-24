"""Review and confirm the current PDF before releasing it to its owner."""
import hmac
import json
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator
from sqlmodel import Session

from ..auth import check_origin
from ..features import require_feature
from ..models import Traveler
from . import completeness, flow
from .form_pdf import FormValue, build_values, load_form, render_pdf
from .router import DocumentIssues, _owned_answers, is_recorded, saved_profile

PRIVATE = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}


class FormReview(BaseModel):
    title: str
    source: str
    fields: list[FormValue]
    manual: list[str]
    report: completeness.Report
    confirmation: str | None


class DownloadConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmation: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviewed: StrictBool
    acknowledged_warnings: StrictBool = False

    @field_validator("reviewed")
    @classmethod
    def explicitly_reviewed(cls, reviewed: bool) -> bool:
        if not reviewed:
            raise ValueError("Confirm you reviewed the form.")
        return reviewed


def create_form_review_router(get_session, current_traveler, load_latest_passport,
                              document_issues: DocumentIssues) -> APIRouter:
    router = APIRouter(prefix="/applications/{application_id}/form", tags=["Embassy PDF"],
                       dependencies=[Depends(require_feature("embassy_pdf"))])
    SessionDep = Annotated[Session, Depends(get_session)]
    TravelerDep = Annotated[Traveler, Depends(current_traveler)]

    def prepare(session: Session, traveler: Traveler, application_id: str, request: Request):
        owned = _owned_answers(session, traveler.id, application_id)
        if owned is None:
            raise HTTPException(status_code=404, detail="Application not found.")
        application, questionnaire, answers = owned
        form = load_form(questionnaire.id)
        if form is None:
            return None, None, []
        form_map, template = form
        today = date.today()
        answers, _ = flow.reconcile(questionnaire, answers, today)
        report = completeness.check(questionnaire, answers, today,
                                    latest_passport=load_latest_passport(traveler.id),
                                    profile=saved_profile(session, traveler.id),
                                    document_issues=document_issues(session, application, answers))
        values = build_values(form_map, questionnaire, answers, traveler.email) if report.ready else []
        fields = [value for _, value, _ in values]
        confirmation = None
        if report.ready and application.archived_at is None and not is_recorded(application):
            payload = json.dumps(["embassy-pdf-v1", traveler.id, application.id, application.updated_at.isoformat(),
                                  form_map.model_dump(mode="json"), answers, traveler.email,
                                  report.model_dump(mode="json")], sort_keys=True).encode()
            confirmation = hmac.digest(request.app.state.auth_settings.jwt_secret.encode(), payload, "sha256").hex()
        manual = form_map.manual
        if application.group_size > 1:
            manual = [*manual, "This PDF uses the saved passport for one traveler. Each traveler needs a separate form."]
        return FormReview(title=form_map.title, source=form_map.source, fields=fields, manual=manual,
                          report=report, confirmation=confirmation), template, values

    @router.get("", response_model=FormReview | None)
    def review(application_id: str, traveler: TravelerDep, session: SessionDep, request: Request, response: Response):
        response.headers.update(PRIVATE)
        return prepare(session, traveler, application_id, request)[0]

    @router.post("/download", dependencies=[Depends(check_origin)], response_class=Response)
    def download(application_id: str, body: DownloadConfirmation, traveler: TravelerDep, session: SessionDep,
                 request: Request):
        review, template, values = prepare(session, traveler, application_id, request)
        if review is None or review.confirmation is None or not hmac.compare_digest(review.confirmation, body.confirmation):
            raise HTTPException(status_code=409, detail="This review is no longer current. Check your application and review it again.")
        if any(issue.severity == "warning" for issue in review.report.issues) and not body.acknowledged_warnings:
            raise HTTPException(status_code=409, detail="Review and acknowledge the warnings before downloading.")
        return Response(render_pdf(template, values), media_type="application/pdf",
                        headers={**PRIVATE, "Content-Disposition": 'attachment; filename="schengen-visa-form.pdf"'})

    return router
