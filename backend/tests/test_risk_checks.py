"""Funds against the embassy minimum (CMPS271-52), refusal-risk flags (CMPS271-54) and the trip questions they read (CMPS271-42/43)."""

from datetime import date, timedelta

import pytest

from app.applications import completeness, flow
from app.applications.risk import funds_minimums, minimum_cents, parse_euros, risk_issues
from test_applications_api import ANSWERS, ARRIVAL

SCHENGEN = flow.load_questionnaire("schengen-short-stay")
TODAY = date.today()


def risks(answers, questionnaire=SCHENGEN, today=TODAY):
    return risk_issues(questionnaire, answers, today)


@pytest.mark.parametrize("text, euros", [
    ("€2,500", 2500), ("2500 EUR", 2500), ("about 3.000 euros", 3000), ("1 200", 1200), ("900.50", 900),
    ("$3000", None), ("2000 USD", None), ("150,000,000 LBP", None), ("a lot", None),
])
def test_declared_funds_are_read_in_euros_only(text, euros):
    assert parse_euros(text) == euros


def test_minimums_follow_each_countrys_published_rule():
    countries = funds_minimums()["countries"]
    assert minimum_cents(countries["FRA"], "hotel", 10) == 65000
    assert minimum_cents(countries["FRA"], "host", 10) == 32500
    assert minimum_cents(countries["DEU"], "rental", 10) == 45000
    assert minimum_cents(countries["PRT"], "hotel", 10) == 7500 + 40000
    assert minimum_cents(countries["ITA"], "hotel", 3) == 26960
    assert minimum_cents(countries["ITA"], "hotel", 15) == 5164 + 15 * 3667
    assert funds_minimums()["source"].startswith("https://eur-lex.europa.eu/")


def test_funds_below_the_minimum_are_flagged_with_the_official_source():
    # Eleven days in France with a host: 11 x €32.50 = €357.50.
    [issue] = [i for i in risks(dict(ANSWERS, funds_amount="€300")) if i.question_id == "funds_amount"]
    assert issue.kind == "risk" and issue.severity == "warning"
    assert "€300" in issue.message and "€357.50" in issue.message and "11 days" in issue.message
    assert issue.source.startswith("https://eur-lex.europa.eu/")
    assert not [i for i in risks(dict(ANSWERS, funds_amount="€2,000")) if i.question_id == "funds_amount"]


def test_missing_or_foreign_currency_funds_ask_for_euros():
    without = {key: value for key, value in ANSWERS.items() if key != "funds_amount"}
    [missing] = [i for i in risks(without) if i.question_id == "funds_amount"]
    assert "Say how much money" in missing.message
    [dollars] = [i for i in risks(dict(ANSWERS, funds_amount="$5000")) if i.question_id == "funds_amount"]
    assert "in euros" in dollars.message


def test_countries_without_a_published_amount_get_no_funds_check():
    assert not [i for i in risks(dict(ANSWERS, main_destination="GRC", funds_amount="€1")) if i.question_id == "funds_amount"]


def test_a_previous_refusal_and_unemployment_are_flagged():
    uk = flow.load_questionnaire("uk-standard-visitor")
    flagged = risks({"refused_visa": True, "previous_uk_visit": False, "employment": "unemployed",
                     "funding": "self"}, uk)
    messages = " ".join(issue.message for issue in flagged)
    assert "refused before" in messages and "Without a job" in messages
    assert {issue.question_id for issue in flagged} >= {"refused_visa", "employment"}


def test_an_arrival_sooner_than_the_usual_decision_time_is_flagged():
    soon = dict(ANSWERS, arrival_date=(TODAY + timedelta(days=5)).isoformat(),
                departure_date=(TODAY + timedelta(days=9)).isoformat(), funds_amount="€5000")
    [issue] = [i for i in risks(soon) if i.question_id == "arrival_date"]
    assert "in 5 days" in issue.message and "15 days" in issue.message
    assert not [i for i in risks(dict(ANSWERS, funds_amount="€5000")) if i.question_id == "arrival_date"]


def test_risks_are_warnings_so_a_complete_application_stays_ready():
    report = completeness.check(SCHENGEN, dict(ANSWERS, funds_amount="€10"), TODAY)
    assert report.ready is True
    assert any(issue.kind == "risk" for issue in report.issues)


def test_every_questionnaire_asks_which_places_the_trip_visits():
    for path in sorted(flow.QUESTIONNAIRE_DIR.glob("*.json")):
        questionnaire = flow.load_questionnaire(path.stem)
        ids = {question.id for question in questionnaire.questions()}
        assert ids & {"itinerary_cities", "places_to_visit"}, path.stem
        assert {"arrival_date", "departure_date"} <= ids, path.stem
    assert "funds_amount" in {question.id for question in SCHENGEN.questions()}
    assert SCHENGEN.question("funds_amount").required is False
    assert ARRIVAL > TODAY
