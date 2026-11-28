"""Public visa guides: what each supported visa needs, before starting an application (Sprint 5).

One page per questionnaire: who needs the visa (CMPS271-19), the document and eligibility checklist
(CMPS271-20), what Ta'asheera does for that embassy (CMPS271-21), official sources (CMPS271-22), fees
and waiting times (CMPS271-24), all in plain language (CMPS271-25).

The checklist and eligibility rules come from the questionnaire files, so the guide always matches the
application. Fees, times and visa rules live in visa_guides.json with their official source and the
date they were checked: update `checked_on` whenever a figure is re-checked. Behind `visa_guide`.
"""

import json
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..features import require_feature
from . import flow
from .policy_sources import SOURCES

GUIDES_FILE = Path(__file__).with_name("visa_guides.json")
# Sources must be official. A new site is a deliberate change here.
OFFICIAL_DOMAINS = ("europa.eu", "gov.uk", "state.gov", "cbp.gov", "canada.ca", "auswaertiges-amt.de",
                    "mzv.gov.cz", "mfa.gov.cn", "nia.gov.cn", "visaforchina.cn", "mofa.go.jp", "emb-japan.go.jp",
                    "indianvisaonline.gov.in", "indianembassybeirut.gov.in", "mfa.gov.tr", "evisa.gov.tr")
CACHE = "public, max-age=3600"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Link(_Strict):
    label: str = Field(min_length=1, max_length=120)
    url: str

    @field_validator("url")
    @classmethod
    def official(cls, url: str) -> str:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        if parsed.scheme != "https" or not any(host == d or host.endswith("." + d) for d in OFFICIAL_DOMAINS):
            raise ValueError(f"{url} is not an https link to an allowed official site.")
        return url


class NeedVisa(_Strict):
    rule: str = Field(min_length=1)
    notes: list[str]
    checker: Link
    # Nationalities (ICAO codes, as on passports) checked against the official list on `checked_on`.
    checked_nationalities: dict[str, Literal["visa", "no_visa"]]


class Fee(_Strict):
    label: str = Field(min_length=1)
    amount: str = Field(min_length=1, max_length=20)


class Fees(_Strict):
    items: list[Fee] = Field(min_length=1)
    note: str
    source: Link


class Timing(_Strict):
    text: str = Field(min_length=1)
    source: Link


class Coverage(_Strict):
    level: Literal["filled_for_you", "guided"]
    text: str = Field(min_length=1)


class GuideData(_Strict):
    questionnaire_id: str
    need: NeedVisa
    fees: Fees
    time: Timing
    coverage: Coverage
    extra_sources: list[Link]


class GuidesFile(_Strict):
    checked_on: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    guides: list[GuideData]


class Document(BaseModel):
    label: str
    help: str | None
    required: bool
    # Plain words for when it is needed, such as: Only if your answer to "Will you stay with a host?" is Yes.
    only_if: str | None


class VisaGuide(BaseModel):
    id: str
    title: str
    destination_label: str
    destination_description: str
    visa_label: str
    visa_description: str
    official_page: Link
    need: NeedVisa
    # Who the visa is not for, in the questionnaire's own words.
    not_for: list[str]
    documents: list[Document]
    fees: Fees
    time: Timing
    coverage: Coverage
    sources: list[Link]
    checked_on: str


def _answer_words(question: flow.Question, values) -> str:
    labels = {option.value: option.label for option in question.options or []}
    words = [f'"{("Yes" if value else "No") if isinstance(value, bool) else labels.get(value, str(value))}"'
             for value in values]
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " or " + words[-1]


def _only_if(questionnaire: flow.Questionnaire, condition: flow.Condition | None) -> str | None:
    if condition is None:
        return None
    question = questionnaire.question(condition.question)
    if question is None:  # The questionnaire loader already refuses this; never show a broken sentence.
        return None
    return f'Only if your answer to "{question.prompt}" is {_answer_words(question, condition.values)}.'


def build_guide(questionnaire: flow.Questionnaire, data: GuideData, checked_on: str) -> VisaGuide:
    title, url, _ = SOURCES[questionnaire.id]
    official = Link(label=title, url=url)
    not_for = [question.stop_if.message for question in questionnaire.questions() if question.stop_if]
    documents = [Document(label=document.label, help=document.help, required=document.required,
                          only_if=_only_if(questionnaire, document.show_if))
                 for document in questionnaire.documents]
    # Every link the page shows, without repeats, the official visa page first.
    sources: list[Link] = []
    for link in (official, data.need.checker, data.fees.source, data.time.source, *data.extra_sources):
        if link.url not in {known.url for known in sources}:
            sources.append(link)
    return VisaGuide(id=questionnaire.id, title=questionnaire.title, destination_label=questionnaire.destination_label,
                     destination_description=questionnaire.destination_description,
                     visa_label=questionnaire.visa_label, visa_description=questionnaire.visa_description,
                     official_page=official, need=data.need, not_for=not_for, documents=documents, fees=data.fees,
                     time=data.time, coverage=data.coverage, sources=sources, checked_on=checked_on)


@lru_cache(maxsize=1)
def load_guides() -> tuple[VisaGuide, ...]:
    """Every packaged guide, in the questionnaires' order. Fails loudly if the data and questionnaires disagree."""
    data = GuidesFile.model_validate(json.loads(GUIDES_FILE.read_text(encoding="utf-8")))
    by_id = {guide.questionnaire_id: guide for guide in data.guides}
    questionnaires = [flow.packaged_questionnaire(path.stem) for path in sorted(flow.QUESTIONNAIRE_DIR.glob("*.json"))]
    missing = {q.id for q in questionnaires} ^ set(by_id)
    if missing:
        raise ValueError(f"visa_guides.json and the questionnaires disagree about: {sorted(missing)}")
    return tuple(build_guide(q, by_id[q.id], data.checked_on) for q in questionnaires)


PUBLISHED: dict[str, tuple[GuideData, str]] = {}


def current_guides() -> tuple[VisaGuide, ...]:
    """The guides travelers see: a version published in the back-office (CMPS271-123) in place of the packaged one.

    `PUBLISHED` holds those facts and their `checked_on` date by questionnaire id; app.requirements replaces it
    together with flow.PUBLISHED.
    """
    questionnaires = {q.id: q for q in flow.available_questionnaires()}
    published = PUBLISHED
    return tuple(build_guide(questionnaires[guide.id], *published[guide.id]) if guide.id in published else guide
                 for guide in load_guides())


def create_visa_guides_router() -> APIRouter:
    router = APIRouter(prefix="/visa-guides", tags=["Visa guides"],
                       dependencies=[Depends(require_feature("visa_guide"))])

    @router.get("", response_model=list[VisaGuide])
    def list_guides(response: Response):
        """Every supported visa's guide. Public: no sign-in needed."""
        response.headers["Cache-Control"] = CACHE
        return current_guides()

    @router.get("/{guide_id}", response_model=VisaGuide)
    def get_guide(guide_id: str, response: Response):
        guide = next((guide for guide in current_guides() if guide.id == guide_id), None)
        if guide is None:
            raise HTTPException(status_code=404, detail="We don't have a guide for that visa.")
        response.headers["Cache-Control"] = CACHE
        return guide

    return router
