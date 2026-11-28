"""Step-by-step guide for destinations whose official application is an online form (UK, US, Canada, ...).

Ta'asheera cannot submit to these portals for the traveler: they need the traveler's own account, a CAPTCHA,
a payment and a signed declaration. So it lays out the portal's own sections in order, each field with the
value from the traveler's confirmed answers, ready to copy, and lists what only the traveler can answer.

Guides are versioned data files in `portals/`, checked against their questionnaire when loaded, like the
Schengen PDF field map. Values are computed from the same reconciled answers the completeness check uses.
"""
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session

from ..models import Traveler
from . import flow
from .form_wording import FIXED_ON_FORMS, OCCUPATION_ON_FORMS, SEX_ON_FORMS
from .router import _owned_answers

DIRECTORY = Path(__file__).with_name("portals")
PRIVATE = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}


class Link(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str
    url: str = Field(pattern=r"^https://")


class GuideField(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str
    # Question ids ("passport.surname", "home_address.city"); the first answered one is used.
    # "account_email" is the sign-in email. Empty for fields only the traveler can fill.
    sources: list[str] = []
    formatter: Literal["answer", "date", "address", "occupation", "country", "fixed"] = "answer"
    fixed: str | None = None
    note: str | None = None


class GuideSection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str
    intro: str | None = None
    fields: list[GuideField] = []
    # Questions on the portal that Ta'asheera doesn't ask, for the traveler to answer there.
    yourself: list[str] = []


class PortalGuide(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str
    portal: Link
    date_format: str
    before: list[str]
    sections: list[GuideSection]
    after: list[str]
    sources: list[Link]


class GuideValue(BaseModel):
    label: str
    value: str | None
    kind: Literal["text", "country", "address"]
    source: str | None
    note: str | None


class GuideSectionValues(BaseModel):
    title: str
    intro: str | None
    fields: list[GuideValue]
    yourself: list[str]


class GuideView(BaseModel):
    title: str
    portal: Link
    before: list[str]
    sections: list[GuideSectionValues]
    after: list[str]
    sources: list[Link]
    filled: int
    total: int


def read_guide(questionnaire_id: str) -> PortalGuide | None:
    path = DIRECTORY / f"{questionnaire_id}.json"
    if not path.is_file():
        return None
    return PortalGuide.model_validate_json(path.read_bytes())


def check_guide(guide: PortalGuide, questionnaire: flow.Questionnaire) -> None:
    """Every field's questions exist; also run on a questionnaire edited in the back-office (CMPS271-123)."""
    for section in guide.sections:
        for field in section.fields:
            for source in field.sources:
                if source != "account_email" and not questionnaire.question(source.split(".")[0]):
                    raise ValueError(f"Unknown question {source} in portal guide {questionnaire.id}.")
            if (field.formatter == "fixed") != (field.fixed is not None):
                raise ValueError(f"Invalid fixed field in portal guide {questionnaire.id}.")
    date.today().strftime(guide.date_format)


@lru_cache(maxsize=None)
def load_guide(questionnaire_id: str) -> PortalGuide | None:
    guide = read_guide(questionnaire_id)
    if guide is not None:
        check_guide(guide, flow.load_questionnaire(questionnaire_id))
    return guide


def _value(field: GuideField, questionnaire: flow.Questionnaire, answers: flow.Answers, email: str,
           date_format: str) -> tuple[str | None, str | None]:
    """The value for one portal field and the question it came from."""
    for path in field.sources:
        key, _, part = path.partition(".")
        question = questionnaire.question(key) if key != "account_email" else None
        raw = answers.get(key) if question else email
        if part:
            raw = raw.get(part) if isinstance(raw, dict) else None
        if raw is None or raw == "":
            continue
        source = question.prompt if question else "Sign-in email"
        match field.formatter:
            case "fixed":
                return FIXED_ON_FORMS.get(field.fixed, field.fixed), source
            case "date":
                return date.fromisoformat(raw).strftime(date_format), source
            case "address":
                return ", ".join(str(raw[name]) for name in ("line1", "line2", "city", "region", "postal_code", "country")
                                 if raw.get(name)), source
            case "occupation":
                return OCCUPATION_ON_FORMS.get(str(raw), flow.display_value(question, raw) if question else str(raw)), source
            case "country":
                return str(raw), source
        if isinstance(raw, bool):
            return ("Yes" if raw else "No"), source
        if part == "sex":
            return SEX_ON_FORMS.get(str(raw), str(raw)), source
        return (flow.display_value(question, raw) if question and not part else str(raw)), source
    return None, None


def build_guide(guide: PortalGuide, questionnaire: flow.Questionnaire, answers: flow.Answers, email: str) -> GuideView:
    sections = []
    for section in guide.sections:
        values = []
        for field in section.fields:
            value, source = _value(field, questionnaire, answers, email, guide.date_format)
            # A country answer is a code; the page shows its name.
            kind = field.formatter if field.formatter in ("country", "address") and value else "text"
            values.append(GuideValue(label=field.label, value=value, kind=kind, source=source, note=field.note))
        sections.append(GuideSectionValues(title=section.title, intro=section.intro, fields=values, yourself=section.yourself))
    filled = sum(1 for section in sections for field in section.fields if field.value)
    total = sum(len(section.fields) for section in sections)
    return GuideView(title=guide.title, portal=guide.portal, before=guide.before, sections=sections, after=guide.after,
                     sources=guide.sources, filled=filled, total=total)


def create_portal_guide_router(get_session, current_traveler) -> APIRouter:
    router = APIRouter(prefix="/applications/{application_id}/portal-guide", tags=["Official online form"])
    SessionDep = Annotated[Session, Depends(get_session)]
    TravelerDep = Annotated[Traveler, Depends(current_traveler)]

    @router.get("", response_model=GuideView)
    def portal_guide(application_id: str, traveler: TravelerDep, session: SessionDep, response: Response):
        response.headers.update(PRIVATE)
        owned = _owned_answers(session, traveler.id, application_id)
        if owned is None:
            raise HTTPException(status_code=404, detail="Application not found.")
        _, questionnaire, answers = owned
        guide = load_guide(questionnaire.id)
        if guide is None:
            raise HTTPException(status_code=404, detail="This visa has no online form guide.")
        answers, _ = flow.reconcile(questionnaire, answers, date.today())
        return build_guide(guide, questionnaire, answers, traveler.email)

    return router
