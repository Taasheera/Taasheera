"""Duplicating an application for another country (US 78, CMPS271-84)."""

from datetime import date, timedelta
import json
from pathlib import Path

import pytest
from sqlmodel import Session, select

from app.applications import flow
from app.applications.records import copy_answers
from app.models import Application, ApplicationAnswer
from test_applications_api import ANSWERS, answer_all, api, put, start  # noqa: F401 (api is a fixture)

SCHENGEN = {"destination": "SCHENGEN", "visa_type": "short_stay"}
QUESTIONNAIRE = Path(__file__).parents[1] / "app" / "applications" / "questionnaires" / "schengen-short-stay.json"


def duplicate(client, headers, application_id, **body):
    return client.post(f"/applications/{application_id}/duplicate", headers=headers, json={**SCHENGEN, **body})


def answers_of(app, application_id):
    with Session(app.state.engine) as session:
        rows = session.exec(select(ApplicationAnswer).where(ApplicationAnswer.application_id == application_id)).all()
        return {row.question_id: (row.value, row.source) for row in rows}


def test_a_copy_for_another_country_keeps_the_answers_that_still_apply(api):
    client, app, headers, _ = api
    original = start(client, headers[0])["id"]
    answer_all(client, headers[0], original)
    before = answers_of(app, original)

    response = duplicate(client, headers[0], original, place="ITA")
    assert response.status_code == 201
    assert response.headers["cache-control"] == "private, no-store"
    copy = response.json()
    assert copy["id"] != original
    # Where the traveler stays belongs to the old country, so it is asked again.
    assert copy["place"] == "Italy" and copy["status"] == "DRAFT"
    assert copy["submitted_at"] is None and copy["archived_at"] is None
    copied = answers_of(app, copy["id"])
    assert copied["main_destination"] == ("ITA", "traveler")
    asked_again = {"main_destination", "accommodation", "host_details"}
    assert {key: value for key, value in copied.items() if key not in asked_again} == \
        {key: value for key, value in before.items() if key not in asked_again}
    assert "accommodation" not in copied and "host_details" not in copied
    assert client.get(f"/applications/{copy['id']}", headers=headers[0]).json()["current"]["id"] == "accommodation"
    # The original is unchanged.
    assert answers_of(app, original) == before
    assert client.get(f"/applications/{original}", headers=headers[0]).json()["place"] == "France"
    assert {item["id"] for item in client.get("/applications", headers=headers[0]).json()} == {original, copy["id"]}
    # The copy is a normal application: it can be changed on its own.
    assert put(client, headers[0], copy["id"], "occupation", "student").status_code == 200
    assert answers_of(app, original)["occupation"] == before["occupation"]


def test_the_choices_leave_out_the_original_country(api):
    client, _, headers, _ = api
    original = start(client, headers[0])["id"]
    response = client.get(f"/applications/{original}/duplicate", headers=headers[0])
    assert response.status_code == 200
    choice, *others = response.json()
    assert len(choice["places"]) == 29  # no country chosen yet: all of them
    # Other destinations can be chosen too; they have no country question of their own.
    assert {other["destination"] for other in others} == {"CAN", "GBR", "USA"}
    assert all(other["place_prompt"] is None and other["places"] == [] for other in others)

    put(client, headers[0], original, "main_destination", "FRA")
    choice = client.get(f"/applications/{original}/duplicate", headers=headers[0]).json()[0]
    assert choice["visa_type"] == "short_stay" and "main destination" in choice["place_prompt"]
    values = [place["value"] for place in choice["places"]]
    assert "FRA" not in values and "ITA" in values and len(values) == 28


@pytest.mark.parametrize("body, detail", [
    ({"place": "FRA"}, "different country"),
    ({}, "Choose the country"),
    ({"place": "XXX"}, "countries offered"),
    ({"place": "ITA", "visa_type": "long_stay"}, "can't prepare"),
])
def test_bad_choices_are_refused_and_nothing_is_created(api, body, detail):
    client, app, headers, _ = api
    original = start(client, headers[0])["id"]
    put(client, headers[0], original, "main_destination", "FRA")
    response = duplicate(client, headers[0], original, **body)
    assert response.status_code == 422
    assert detail in response.json()["detail"]
    with Session(app.state.engine) as session:
        assert len(session.exec(select(Application)).all()) == 1


@pytest.mark.parametrize("body", [{"place": "ITA", "extra": 1}, {"place": ""}, {"place": "I" * 41}])
def test_duplicate_bodies_are_strict(api, body):
    client, _, headers, _ = api
    original = start(client, headers[0])["id"]
    assert duplicate(client, headers[0], original, **body).status_code == 422


def test_only_the_owner_can_duplicate(api):
    client, app, headers, _ = api
    original = start(client, headers[0])["id"]
    assert duplicate(client, headers[1], original, place="ITA").status_code == 404
    assert client.get(f"/applications/{original}/duplicate", headers=headers[1]).status_code == 404
    assert duplicate(client, headers[0], "f" * 32, place="ITA").status_code == 404
    assert client.post(f"/applications/{original}/duplicate", json={**SCHENGEN, "place": "ITA"},
                       headers={**headers[0], "Origin": "https://evil.invalid"}).status_code == 403
    with Session(app.state.engine) as session:
        assert len(session.exec(select(Application)).all()) == 1


def test_a_submitted_application_copies_without_its_record_or_past_dates(api):
    client, app, headers, _ = api
    original = start(client, headers[0])["id"]
    answer_all(client, headers[0], original)
    client.post(f"/applications/{original}/decision", headers=headers[0], json={"record": "SUBMITTED"})
    client.post(f"/applications/{original}/decision", headers=headers[0], json={"record": "REFUSED"})
    # The refused trip has passed.
    past = (date.today() - timedelta(days=40)).isoformat()
    with Session(app.state.engine) as session:
        for question_id in ("arrival_date", "departure_date"):
            row = session.get(ApplicationAnswer, (original, question_id))
            row.value = past
            session.add(row)
        session.commit()

    copy = duplicate(client, headers[0], original, place="ESP").json()
    assert copy["status"] == "DRAFT" and copy["submitted_at"] is None and copy["decided_at"] is None
    copied = answers_of(app, copy["id"])
    assert "arrival_date" not in copied and "departure_date" not in copied
    assert copied["occupation"] == answers_of(app, original)["occupation"]
    state = client.get(f"/applications/{copy['id']}", headers=headers[0]).json()
    assert state["current"]["id"] == "arrival_date"
    assert client.get(f"/applications/{original}", headers=headers[0]).json()["status"] == "REFUSED"


def test_archived_applications_can_be_duplicated(api):
    client, _, headers, _ = api
    original = start(client, headers[0])["id"]
    answer_all(client, headers[0], original, stop_at="passport")
    client.post(f"/applications/{original}/archive", headers=headers[0])
    copy = duplicate(client, headers[0], original, place="ITA")
    assert copy.status_code == 201
    assert copy.json()["archived_at"] is None and copy.json()["status"] == "DRAFT"


def test_only_matching_questions_of_the_same_type_are_copied():
    source = flow.load_questionnaire("schengen-short-stay")
    raw = json.loads(QUESTIONNAIRE.read_text(encoding="utf-8"))
    raw["id"] = "test-copy-target"
    # The target no longer asks the occupation question, and asks the phone as free text.
    for step in raw["steps"]:
        for question in step["questions"]:
            if question["id"] == "phone":
                question["type"] = "text"
                question.pop("prefill", None)
        step["questions"] = [question for question in step["questions"] if question["id"] not in ("occupation",)]
    for step in raw["steps"]:
        for question in step["questions"]:
            if question.get("show_if", {}).get("question") == "occupation":
                question.pop("show_if")
    for document in raw.get("documents", []):
        if document.get("show_if", {}).get("question") == "occupation":
            document.pop("show_if")
    target = flow.Questionnaire.model_validate(raw)
    answers, _ = flow.reconcile(source, dict(ANSWERS), date.today())
    copied = copy_answers(source, target, answers, "ITA", date.today())
    assert copied["main_destination"] == "ITA"
    assert "occupation" not in copied and "phone" not in copied
    assert copied["purpose"] == "tourism"
    with pytest.raises(flow.InvalidAnswer):
        copy_answers(source, target, answers, "MARS", date.today())


def test_a_departure_date_is_not_copied_without_its_arrival(api):
    """QA: the original's arrival date has passed, so the copy asks for both dates again."""
    client, app, headers, _ = api
    original = start(client, headers[0])["id"]
    answer_all(client, headers[0], original)
    with Session(app.state.engine) as session:
        row = session.get(ApplicationAnswer, (original, "arrival_date"))
        row.value = (date.today() - timedelta(days=5)).isoformat()
        session.add(row)
        session.commit()
    copy = duplicate(client, headers[0], original, place="ITA").json()
    copied = answers_of(app, copy["id"])
    assert "arrival_date" not in copied and "departure_date" not in copied
    assert copied["occupation"] == ("retired", "traveler")


def test_visas_without_a_list_of_countries_are_not_offered(api, monkeypatch):
    client, _, headers, _ = api
    original = start(client, headers[0])["id"]
    raw = json.loads(QUESTIONNAIRE.read_text(encoding="utf-8"))
    raw.update(id="test-country-place", visa_type="test_visa")
    place = dict(raw["steps"][0]["questions"][0], type="country")
    place.pop("options")
    raw["steps"][0]["questions"][0] = place
    other = flow.Questionnaire.model_validate(raw)
    real = flow.available_questionnaires()
    monkeypatch.setattr(flow, "available_questionnaires", lambda: [*real, other])
    choices = client.get(f"/applications/{original}/duplicate", headers=headers[0]).json()
    assert "test_visa" not in [choice["visa_type"] for choice in choices]
    assert choices[0]["visa_type"] == "short_stay"


def test_a_copy_for_another_destination_keeps_the_shared_answers(api):
    client, app, headers, _ = api
    original = start(client, headers[0])["id"]
    answer_all(client, headers[0], original)
    response = client.post(f"/applications/{original}/duplicate", headers=headers[0],
                           json={"destination": "USA", "visa_type": "b2_tourist"})
    assert response.status_code == 201
    copy = response.json()
    assert copy["destination"] == "USA" and copy["status"] in ("DRAFT", "INELIGIBLE", "ANSWERS_COMPLETE")
    copied = answers_of(app, copy["id"])
    assert "main_destination" not in copied and "accommodation" not in copied
    assert copied  # some answers, such as the passport, are asked by both questionnaires
    # A visa without a country question takes no country.
    assert client.post(f"/applications/{original}/duplicate", headers=headers[0],
                       json={"destination": "USA", "visa_type": "b2_tourist", "place": "ITA"}).status_code == 422
