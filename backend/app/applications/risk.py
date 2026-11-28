"""Funds against the embassy's minimum (CMPS271-52) and refusal-risk flags (CMPS271-54) for the completeness check.

Pure, like the check itself: answers in, warnings out. Nothing here blocks an application, because only the
embassy decides; each flag names what raises the risk and what the traveler can do about it. The funds
minimums are the reference amounts Schengen states publish (`funds_minimums.json`, official source and the
date they were checked). A destination without a published amount gets no funds check.
"""
from datetime import date
from functools import cache
import json
from pathlib import Path
import re

from . import flow

FUNDS_FILE = Path(__file__).with_name("funds_minimums.json")
REFUSAL_QUESTIONS = ("previous_refusal", "refused_visa", "refused_us_visa")
# How long a decision often takes, in days, from each destination's own guidance (visa_guides.json).
DECISION_DAYS = {"SCHENGEN": 15, "GBR": 21, "CAN": 30}
_OTHER_CURRENCY = re.compile(r"\$|£|¥|\b(usd|gbp|cad|lbp|l\.?l\.?|dollars?|pounds?|lira|lire)\b", re.IGNORECASE)
_NUMBER = re.compile(r"(\d[\d,.' ]*)\s*(k\b|thousand\b)?", re.IGNORECASE)


@cache
def funds_minimums() -> dict:
    return json.loads(FUNDS_FILE.read_text(encoding="utf-8"))


def parse_euros(text: str) -> int | None:
    """Whole euros from an answer such as "€2,500", "2500 EUR" or "about 3.000 euros"; None when it isn't euros."""
    if _OTHER_CURRENCY.search(text):
        return None
    match = _NUMBER.search(text)
    if match is None:
        return None
    number = match.group(1).strip().replace(" ", "").replace("'", "").rstrip(".,")
    # "2.500" and "2,500" are two and a half thousand (three digits after the mark); "2,5" and "1.5" are decimals.
    whole, fraction = number, ""
    decimal = re.search(r"[.,](\d{1,2})$", number)
    if decimal:
        whole, fraction = number[:decimal.start()], decimal.group(1)
    digits = re.sub(r"[.,]", "", whole)
    if not digits.isdigit():
        return None
    value = float(f"{digits}.{fraction or 0}")
    if match.group(2):
        value *= 1000
    return int(value)


def minimum_cents(country: dict, accommodation: str | None, days: int) -> int:
    if "bands" in country:
        band = next(band for band in country["bands"] if band["max_days"] is None or days <= band["max_days"])
        return band["fixed"] + band["per_day"] * (days if band["per_day"] else 0)
    per_day = country["per_day"]
    daily = per_day.get(accommodation if accommodation in ("hotel", "host") else "other", per_day["other"])
    return country.get("per_entry", 0) + daily * days


def _euros(cents: int) -> str:
    return f"€{cents / 100:,.0f}" if cents % 100 == 0 else f"€{cents / 100:,.2f}"


def funds_issue(questionnaire: flow.Questionnaire, answers: flow.Answers):
    """A warning when the money the traveler declared is below the main destination's published minimum."""
    from .completeness import Issue

    if questionnaire.destination != "SCHENGEN" or "funds_amount" not in {q.id for q in questionnaire.questions()}:
        return None
    data = funds_minimums()
    country = data["countries"].get(answers.get("main_destination") or "")
    try:
        arrival, departure = date.fromisoformat(answers["arrival_date"]), date.fromisoformat(answers["departure_date"])
    except (KeyError, TypeError, ValueError):
        return None
    if country is None or departure < arrival:
        return None
    days = (departure - arrival).days + 1
    minimum = minimum_cents(country, answers.get("accommodation"), days)
    where = dict(question_id="funds_amount", step_id=questionnaire.step_of("funds_amount").id,
                 source=country.get("source", data["source"]))
    declared = answers.get("funds_amount")
    if not isinstance(declared, str) or not declared.strip():
        # The question is optional: no amount, nothing to compare.
        return None
    euros = parse_euros(declared)
    if euros is None:
        return Issue(kind="risk", severity="warning", **where, message=(
            f"Give the money you'll have in euros, so it can be compared with {country['label']}'s minimum of "
            f"about {_euros(minimum)} for {days} days."))
    if euros * 100 < minimum:
        return Issue(kind="risk", severity="warning", **where, message=(
            f"You'll have about {_euros(euros * 100)}, below the {_euros(minimum)} {country['label']} expects for "
            f"{days} days. Show more savings or a sponsor's support, or the visa may be refused."))
    return None


def risk_issues(questionnaire: flow.Questionnaire, answers: flow.Answers, today: date) -> list:
    """Answers that often lead to a refusal, each with what the traveler can do."""
    from .completeness import Issue

    issues = []
    visible = {question.id for question in flow.visible_questions(questionnaire, answers)}

    def at(question_id: str) -> dict:
        return dict(question_id=question_id, step_id=questionnaire.step_of(question_id).id)

    refused = next((q for q in REFUSAL_QUESTIONS if q in visible and answers.get(q) is True), None)
    if refused:
        issues.append(Issue(kind="risk", severity="warning", **at(refused), message=(
            "You've had a visa refused before. Explain what has changed in your cover letter, and bring the "
            "refusal letter and evidence that answers the reason given.")))
    work = next((q for q in ("occupation", "employment") if q in visible), None)
    if work and answers.get(work) == "unemployed" and answers.get("funding", "self") == "self":
        issues.append(Issue(kind="risk", severity="warning", **at(work), message=(
            "Without a job or regular income, embassies look closely at how you'll pay and why you'll return. "
            "Bring recent bank statements, a sponsor's letter if someone helps, and proof of ties at home "
            "such as family, property or studies.")))
    wait = DECISION_DAYS.get(questionnaire.destination)
    if wait and isinstance(answers.get("arrival_date"), str):
        try:
            days_left = (date.fromisoformat(answers["arrival_date"]) - today).days
        except ValueError:
            days_left = None
        if days_left is not None and 0 <= days_left < wait:
            issues.append(Issue(kind="risk", severity="warning", **at("arrival_date"), message=(
                f"You arrive in {days_left} {'day' if days_left == 1 else 'days'}, but a decision often takes "
                f"{wait} days or more. Apply now, ask about an urgent appointment, or move your dates.")))
    funds = funds_issue(questionnaire, answers)
    if funds is not None:
        issues.append(funds)
    return issues
