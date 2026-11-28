from datetime import date, timedelta
from urllib.parse import urlparse

import pytest
from sqlmodel import Session, select

from app.applications import flow
from app.applications.flow import add_months
from app.applications.passport_rules import BLANK_PAGES_LINK, load_rules, passport_issues, rule_for, summary
from app.models import PassportPages
from test_applications_api import ANSWERS, ARRIVAL, SPECIMEN, answer_all, api, start  # noqa: F401

TODAY = date(2027, 1, 10)
ARRIVE = date(2027, 3, 1)
LEAVE = date(2027, 3, 15)


def issues(questionnaire_id, expiry, *, arrival=ARRIVE, departure=LEAVE, issued=None, pages=5, today=TODAY):
    answers = {"passport": {**SPECIMEN, "expiry_date": expiry.isoformat()}}
    if arrival:
        answers["arrival_date"] = arrival.isoformat()
    if departure:
        answers["departure_date"] = departure.isoformat()
    if issued:
        answers["passport_issue_date"] = issued.isoformat()
    return passport_issues(flow.load_questionnaire(questionnaire_id), answers, today, blank_pages=pages)


def test_every_questionnaire_has_a_rule_with_official_sources():
    rules = load_rules()
    assert {rule.questionnaire_id for rule in rules.rules} == {q.id for q in flow.available_questionnaires()}
    for rule in rules.rules:
        for source in rule.sources:
            assert urlparse(source.url).scheme == "https"


def test_schengen_needs_three_months_after_departure_to_the_day():
    needed = add_months(LEAVE, 3)
    assert issues("schengen-short-stay", needed) == []
    [issue] = issues("schengen-short-stay", needed - timedelta(days=1))
    assert (issue.kind, issue.severity, issue.question_id, issue.step_id) == ("ineligible", "blocking", "passport", "passport")
    assert "15 June 2027" in issue.message and "3 months after you leave" in issue.message
    assert issue.source == rule_for("schengen-short-stay").sources[0].url
    assert SPECIMEN["document_number"] not in issue.message


def test_without_a_departure_date_the_arrival_is_the_earliest_departure():
    [issue] = issues("schengen-short-stay", add_months(ARRIVE, 3) - timedelta(days=1), departure=None)
    assert "1 June 2027" in issue.message
    assert issues("schengen-short-stay", add_months(ARRIVE, 3), departure=None) == []
    assert issues("schengen-short-stay", date(2027, 2, 1), arrival=None, departure=None) == []


def test_schengen_passport_must_be_issued_within_ten_years_of_arrival():
    assert issues("schengen-short-stay", date(2030, 1, 1), issued=date(2017, 3, 1)) == []
    [issue] = issues("schengen-short-stay", date(2030, 1, 1), issued=date(2017, 2, 28))
    assert issue.question_id == "passport_issue_date" and issue.severity == "blocking"
    assert "10 years" in issue.message
    assert issues("uk-standard-visitor", date(2030, 1, 1), issued=date(2001, 1, 1)) == []


def test_uk_and_japan_need_the_passport_valid_for_the_whole_stay():
    for questionnaire_id in ("uk-standard-visitor", "japan-short-stay"):
        assert issues(questionnaire_id, LEAVE) == []
        [issue] = issues(questionnaire_id, LEAVE - timedelta(days=1))
        assert issue.severity == "blocking" and "whole of your stay" in issue.message


def test_us_warns_about_six_months_beyond_the_stay_and_names_the_exemption():
    assert issues("us-b2-tourist", add_months(LEAVE, 6)) == []
    [warning] = issues("us-b2-tourist", add_months(LEAVE, 6) - timedelta(days=1))
    assert warning.severity == "warning" and "Six-Month Club" in warning.message and "should" in warning.message
    blocking, warning = issues("us-b2-tourist", LEAVE - timedelta(days=1))
    assert (blocking.severity, warning.severity) == ("blocking", "warning")


def test_china_and_india_count_six_months_from_the_day_you_apply():
    for questionnaire_id in ("china-tourist-l", "india-tourist"):
        assert issues(questionnaire_id, add_months(TODAY, 6)) == []
        [issue] = issues(questionnaire_id, add_months(TODAY, 6) - timedelta(days=1))
        assert issue.severity == "blocking" and "10 July 2027" in issue.message


def test_turkiye_needs_sixty_days_beyond_the_stay_and_advises_six_months():
    assert issues("turkiye-evisa", max(LEAVE + timedelta(days=60), add_months(ARRIVE, 6))) == []
    [advice] = issues("turkiye-evisa", LEAVE + timedelta(days=60))
    assert advice.severity == "warning"
    kinds = [issue.severity for issue in issues("turkiye-evisa", LEAVE + timedelta(days=59))]
    assert kinds == ["blocking", "warning"]


def test_canada_blocks_past_the_stay_and_advises_six_months_from_travel():
    assert [i.severity for i in issues("canada-visitor", add_months(ARRIVE, 6) - timedelta(days=1))] == ["warning"]
    assert [i.severity for i in issues("canada-visitor", LEAVE - timedelta(days=1))] == ["blocking", "warning"]


@pytest.mark.parametrize("questionnaire_id, needed", [
    ("schengen-short-stay", 2), ("china-tourist-l", 2), ("india-tourist", 2), ("us-b2-tourist", 1),
    ("canada-visitor", 1), ("japan-short-stay", 1)])
def test_blank_pages_are_checked_against_each_destination(questionnaire_id, needed):
    far = date(2035, 1, 1)
    [missing] = issues(questionnaire_id, far, pages=None)
    assert (missing.kind, missing.severity, missing.link) == ("missing", "warning", BLANK_PAGES_LINK)
    [short] = issues(questionnaire_id, far, pages=needed - 1)
    assert (short.kind, short.severity, short.link) == ("ineligible", "blocking", BLANK_PAGES_LINK)
    assert f"you told us yours has {needed - 1}" in short.message
    assert issues(questionnaire_id, far, pages=needed) == []


@pytest.mark.parametrize("questionnaire_id", ["uk-standard-visitor", "turkiye-evisa"])
def test_destinations_without_a_page_rule_never_ask(questionnaire_id):
    assert issues(questionnaire_id, date(2035, 1, 1), pages=None) == []


def test_nothing_is_checked_before_the_passport_is_answered():
    questionnaire = flow.load_questionnaire("schengen-short-stay")
    assert passport_issues(questionnaire, {"arrival_date": "2027-03-01"}, TODAY, blank_pages=None) == []


def test_summary_lists_each_rule_in_plain_words():
    text = summary(flow.load_questionnaire("schengen-short-stay"))
    assert text.requirements == ["Valid until 3 months after you leave the Schengen area",
                                 "Issued within the previous 10 years", "At least 2 blank visa pages"]
    assert summary(flow.load_questionnaire("uk-standard-visitor")).requirements == [
        "Valid for the whole of your stay in the UK"]
    assert summary(flow.load_questionnaire("us-b2-tourist")).requirements[1].startswith("Advised: valid until 6 months")


def check(client, headers, application_id):
    response = client.get(f"/applications/{application_id}/check", headers=headers)
    assert response.status_code == 200
    return response.json()


def test_blank_pages_declaration_feeds_the_completeness_check(api):
    client, app, headers, _ = api
    application_id = start(client, headers[0])["id"]
    answer_all(client, headers[0], application_id)
    [asked] = [issue for issue in check(client, headers[0], application_id)["issues"] if issue["link"]]
    assert asked["severity"] == "warning" and asked["link"] == "/documents#passport"

    response = client.put("/vault/passport", headers=headers[0], json={"blank_pages": 1})
    assert response.status_code == 200 and response.headers["cache-control"] == "private, no-store"
    body = response.json()
    assert body["blank_pages"] == 1
    [rule] = body["rules"]
    assert rule["questionnaire_id"] == "schengen-short-stay" and rule["blank_pages"] == 2
    assert rule["sources"][0]["url"].startswith("https://eur-lex.europa.eu/")
    report = check(client, headers[0], application_id)
    [short] = [issue for issue in report["issues"] if issue["link"]]
    assert short["severity"] == "blocking" and not report["ready"]

    assert client.put("/vault/passport", headers=headers[0], json={"blank_pages": 4}).status_code == 200
    assert not [issue for issue in check(client, headers[0], application_id)["issues"] if issue["link"]]
    assert client.get("/vault/passport", headers=headers[1]).json() == {"blank_pages": None, "rules": []}
    with Session(app.state.engine) as session:
        assert [(row.traveler_id, row.blank_pages) for row in session.exec(select(PassportPages))] == [(1, 4)]


@pytest.mark.parametrize("body", [{"blank_pages": -1}, {"blank_pages": 101}, {"blank_pages": "two"},
                                  {"blank_pages": 2, "traveler_id": 2}, {}])
def test_the_declaration_is_validated(api, body):
    client, _, headers, _ = api
    assert client.put("/vault/passport", headers=headers[0], json=body).status_code == 422


def test_a_passport_that_runs_out_too_soon_blocks_with_a_link_to_the_passport(api):
    client, _, headers, _ = api
    application_id = start(client, headers[0])["id"]
    short = {**ANSWERS, "passport": {**SPECIMEN, "expiry_date": add_months(ARRIVAL, 2).isoformat()}}
    answer_all(client, headers[0], application_id, short, stop_at="departure_date")
    client.put("/vault/passport", headers=headers[0], json={"blank_pages": 4})
    [issue] = [i for i in check(client, headers[0], application_id)["issues"] if i["kind"] == "ineligible"]
    assert (issue["question_id"], issue["step_id"], issue["severity"]) == ("passport", "passport", "blocking")
    assert issue["source"].startswith("https://")


def test_passport_rules_follow_their_flag(api, monkeypatch):
    client, app, headers, _ = api
    application_id = start(client, headers[0])["id"]
    answer_all(client, headers[0], application_id)
    flags = type(app.state.feature_flags)
    monkeypatch.setattr(app.state, "feature_flags", flags(frozenset({"supporting_documents"})))
    assert not [issue for issue in check(client, headers[0], application_id)["issues"] if issue["link"]]
    assert client.get("/vault/passport", headers=headers[0]).status_code == 404
    assert client.put("/vault/passport", headers=headers[0], json={"blank_pages": 2}).status_code == 404
