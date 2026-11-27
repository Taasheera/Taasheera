import json
from datetime import date, timedelta
from functools import cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import completeness, flow
from .visa_guides import OFFICIAL_DOMAINS

RULES_FILE = Path(__file__).with_name("passport_rules.json")
RULE_DOMAINS = (*OFFICIAL_DOMAINS, "usembassy.gov", "china-embassy.gov.cn")
BLANK_PAGES_LINK = "/documents#passport"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Source(_Strict):
    label: str = Field(min_length=1, max_length=160)
    url: str

    @field_validator("url")
    @classmethod
    def official(cls, url: str) -> str:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        if parsed.scheme != "https" or not any(host == d or host.endswith("." + d) for d in RULE_DOMAINS):
            raise ValueError(f"{url} is not an https link to an allowed official site.")
        return url


class ValidityRule(_Strict):
    after: Literal["arrival", "departure", "application"]
    months: int = Field(default=0, ge=0, le=24)
    days: int = Field(default=0, ge=0, le=366)
    severity: completeness.Severity
    text: str = Field(min_length=1, max_length=160)
    exemption: str | None = None


class PassportRule(_Strict):
    questionnaire_id: str
    validity: list[ValidityRule] = Field(min_length=1)
    issued_within_years: int | None = Field(default=None, ge=1, le=20)
    blank_pages: int | None = Field(default=None, ge=1, le=10)
    note: str | None = None
    sources: list[Source] = Field(min_length=1)


class RulesFile(_Strict):
    checked_on: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    rules: list[PassportRule]


class RuleSummary(BaseModel):
    questionnaire_id: str
    title: str
    requirements: list[str]
    blank_pages: int | None
    note: str | None
    sources: list[Source]
    checked_on: str


@cache
def load_rules() -> RulesFile:
    data = RulesFile.model_validate(json.loads(RULES_FILE.read_text(encoding="utf-8")))
    known = {questionnaire.id for questionnaire in flow.available_questionnaires()}
    listed = [rule.questionnaire_id for rule in data.rules]
    if len(set(listed)) != len(listed) or set(listed) != known:
        raise ValueError(f"passport_rules.json and the questionnaires disagree about: {sorted(set(listed) ^ known)}")
    return data


def rule_for(questionnaire_id: str) -> PassportRule:
    return next(rule for rule in load_rules().rules if rule.questionnaire_id == questionnaire_id)


def _day(value) -> date | None:
    try:
        return date.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


def _shown(day: date) -> str:
    return f"{day.day} {day.strftime('%B %Y')}"


def _pages(count: int) -> str:
    return f"{count} blank visa {'page' if count == 1 else 'pages'}"


def summary(questionnaire: flow.Questionnaire) -> RuleSummary:
    rule = rule_for(questionnaire.id)
    requirements = [f"{'Valid' if item.severity == 'blocking' else 'Advised: valid'} for {item.text}"
                     if item.after == "departure" and not (item.months or item.days)
                     else f"{'Valid' if item.severity == 'blocking' else 'Advised: valid'} until {item.text}"
                     for item in rule.validity]
    if rule.issued_within_years:
        requirements.append(f"Issued within the previous {rule.issued_within_years} years")
    if rule.blank_pages:
        requirements.append(f"At least {_pages(rule.blank_pages)}")
    return RuleSummary(questionnaire_id=questionnaire.id, title=questionnaire.title, requirements=requirements,
                       blank_pages=rule.blank_pages, note=rule.note, sources=rule.sources,
                       checked_on=load_rules().checked_on)


def passport_issues(questionnaire: flow.Questionnaire, answers: flow.Answers, today: date, *,
                    blank_pages: int | None) -> list[completeness.Issue]:
    passport = answers.get("passport")
    if not isinstance(passport, dict):
        return []
    rule = rule_for(questionnaire.id)
    source = rule.sources[0].url
    expiry = _day(passport.get("expiry_date"))
    arrival, departure = _day(answers.get("arrival_date")), _day(answers.get("departure_date"))
    at_passport = dict(question_id="passport", step_id=questionnaire.step_of("passport").id)
    issues: list[completeness.Issue] = []
    for item in rule.validity:
        anchor = {"arrival": arrival, "departure": departure or arrival, "application": today}[item.after]
        if expiry is None or anchor is None:
            continue
        needed = flow.add_months(anchor, item.months) + timedelta(days=item.days)
        if expiry >= needed:
            continue
        must = "must" if item.severity == "blocking" else "should"
        message = (f"For the {questionnaire.title}, your passport {must} be valid until at least {_shown(needed)}: "
                   f"{item.text}. Renew it, or change your travel dates if they can move.")
        if item.exemption:
            message = f"{message} {item.exemption}"
        issues.append(completeness.Issue(kind="ineligible", severity=item.severity, message=message,
                                         source=source, **at_passport))
    issued = _day(answers.get("passport_issue_date"))
    if rule.issued_within_years and issued and questionnaire.question("passport_issue_date"):
        entry = arrival or today
        if flow.add_months(issued, 12 * rule.issued_within_years) < entry:
            issues.append(completeness.Issue(
                kind="ineligible", severity="blocking", source=source, question_id="passport_issue_date",
                step_id=questionnaire.step_of("passport_issue_date").id,
                message=(f"For the {questionnaire.title}, your passport must have been issued within the "
                         f"{rule.issued_within_years} years before you arrive. Renew it before you apply.")))
    if rule.blank_pages:
        if blank_pages is None:
            issues.append(completeness.Issue(
                kind="missing", severity="warning", link=BLANK_PAGES_LINK, source=source,
                message=(f"The {questionnaire.title} needs a passport with at least {_pages(rule.blank_pages)}. "
                         "Tell us how many your passport has on your documents page.")))
        elif blank_pages < rule.blank_pages:
            issues.append(completeness.Issue(
                kind="ineligible", severity="blocking", link=BLANK_PAGES_LINK, source=source,
                message=(f"The {questionnaire.title} needs a passport with at least {_pages(rule.blank_pages)}, "
                         f"and you told us yours has {blank_pages}. Renew it before you apply.")))
    return issues
