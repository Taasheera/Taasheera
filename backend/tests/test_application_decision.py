"""Recording submission and the embassy's decision (US 55, CMPS271-80)."""

from datetime import date, timedelta

import pytest
from sqlmodel import Session, select

from app.applications.router import application_context, application_proposal
from app.applications import flow
from app.models import Application, ApplicationAnswer, Notification
from test_applications_api import ANSWERS, answer_all, api, put, start  # noqa: F401 (api is a fixture)

TITLE = "Schengen short-stay visa (type C)"


def record(client, headers, application_id, status):
    return client.post(f"/applications/{application_id}/decision", headers=headers, json={"status": status})


def timeline(client, headers, application_id):
    return client.get(f"/applications/{application_id}/timeline", headers=headers).json()


def complete(client, headers):
    application_id = start(client, headers)["id"]
    assert answer_all(client, headers, application_id)["status"] == "ANSWERS_COMPLETE"
    return application_id


def test_submitted_then_approved_shows_on_the_dashboard_and_timeline(api):
    client, _, headers, _ = api
    application_id = complete(client, headers[0])
    before = client.get(f"/applications/{application_id}", headers=headers[0]).json()

    response = record(client, headers[0], application_id, "SUBMITTED")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    submitted = response.json()
    assert submitted["status"] == "SUBMITTED"
    assert submitted["submitted_at"] is not None and submitted["decided_at"] is None
    # Recording isn't a change to the answers.
    assert submitted["updated_at"] == before["updated_at"]
    [listed] = client.get("/applications", headers=headers[0]).json()
    assert listed["status"] == "SUBMITTED"
    events = timeline(client, headers[0], application_id)
    assert [event["kind"] for event in events["events"]][-2:] == ["complete", "submitted"]
    assert events["next"]["kind"] == "submitted"

    approved = record(client, headers[0], application_id, "APPROVED").json()
    assert approved["status"] == "APPROVED"
    assert approved["submitted_at"] == submitted["submitted_at"]
    assert approved["decided_at"] is not None
    events = timeline(client, headers[0], application_id)
    assert [event["kind"] for event in events["events"]][-3:] == ["complete", "submitted", "approved"]
    assert events["next"]["kind"] == "approved"
    assert [event["at"] for event in events["events"]] == sorted(event["at"] for event in events["events"])


def test_each_recorded_status_notifies_the_owner(api):
    client, app, headers, _ = api
    application_id = complete(client, headers[0])
    for status in ("SUBMITTED", "REFUSED"):
        assert record(client, headers[0], application_id, status).status_code == 200
    messages = [item["message"] for item in client.get("/notifications", headers=headers[0]).json()]
    assert messages[:2] == [
        f"You recorded that your {TITLE} application was refused.",
        f"You recorded that your {TITLE} application was submitted to the embassy.",
    ]
    # A repeated click is not a second change.
    assert record(client, headers[0], application_id, "REFUSED").status_code == 200
    with Session(app.state.engine) as session:
        assert len(session.exec(select(Notification).where(Notification.application_id == application_id)).all()) == 3


@pytest.mark.parametrize("status", ["SUBMITTED", "APPROVED", "REFUSED", "ANSWERS_COMPLETE"])
def test_an_unfinished_application_cannot_be_recorded(api, status):
    client, _, headers, _ = api
    application_id = start(client, headers[0])["id"]
    answer_all(client, headers[0], application_id, stop_at="passport")
    response = record(client, headers[0], application_id, status)
    assert response.status_code == 409
    assert client.get(f"/applications/{application_id}", headers=headers[0]).json()["status"] == "DRAFT"


def test_a_decision_needs_a_submission_first(api):
    client, _, headers, _ = api
    application_id = complete(client, headers[0])
    response = record(client, headers[0], application_id, "APPROVED")
    assert response.status_code == 409
    assert "submitted" in response.json()["detail"]


def test_a_stale_complete_status_is_rechecked_before_submitting(api):
    client, app, headers, _ = api
    application_id = complete(client, headers[0])
    # The trip date has passed since the last visit: the stored status still says complete.
    with Session(app.state.engine) as session:
        row = session.get(ApplicationAnswer, (application_id, "arrival_date"))
        row.value = (date.today() - timedelta(days=3)).isoformat()
        session.add(row)
        session.commit()
    assert record(client, headers[0], application_id, "SUBMITTED").status_code == 409


def test_mistakes_can_be_taken_back_one_step(api):
    client, _, headers, _ = api
    application_id = complete(client, headers[0])
    record(client, headers[0], application_id, "SUBMITTED")
    refused = record(client, headers[0], application_id, "REFUSED").json()
    # Wrong decision: switch it.
    approved = record(client, headers[0], application_id, "APPROVED").json()
    assert approved["status"] == "APPROVED" and approved["decided_at"] >= refused["decided_at"]
    # No decision yet after all: the submission date stays.
    back = record(client, headers[0], application_id, "SUBMITTED").json()
    assert back["status"] == "SUBMITTED" and back["decided_at"] is None
    assert back["submitted_at"] == refused["submitted_at"]
    assert [event["kind"] for event in timeline(client, headers[0], application_id)["events"]][-1] == "submitted"
    # Not submitted after all: back to answering.
    undone = record(client, headers[0], application_id, "ANSWERS_COMPLETE").json()
    assert undone["status"] == "ANSWERS_COMPLETE"
    assert undone["submitted_at"] is None and undone["decided_at"] is None
    assert put(client, headers[0], application_id, "occupation", "unemployed").status_code == 200


def test_recorded_answers_are_frozen(api):
    client, app, headers, _ = api
    application_id = complete(client, headers[0])
    record(client, headers[0], application_id, "SUBMITTED")
    response = put(client, headers[0], application_id, "occupation", "unemployed")
    assert response.status_code == 409
    assert "submitted" in response.json()["detail"]

    # After the trip the dates are in the past; the record still shows what was submitted.
    past = (date.today() - timedelta(days=40)).isoformat()
    with Session(app.state.engine) as session:
        for question_id, value in (("arrival_date", past), ("departure_date", past)):
            row = session.get(ApplicationAnswer, (application_id, question_id))
            row.value = value
            session.add(row)
        session.commit()
    state = client.get(f"/applications/{application_id}", headers=headers[0]).json()
    assert state["status"] == "SUBMITTED"
    assert {answer["question"]["id"] for answer in state["answers"]} >= {"arrival_date", "departure_date"}
    assert client.get(f"/applications/{application_id}/check", headers=headers[0]).status_code == 200
    with Session(app.state.engine) as session:
        assert session.get(ApplicationAnswer, (application_id, "arrival_date")) is not None
        assert session.get(Application, application_id).status == "SUBMITTED"
    assert timeline(client, headers[0], application_id)["next"]["kind"] == "submitted"


def test_the_assistant_sees_the_record_and_cannot_change_answers(api):
    client, app, headers, _ = api
    application_id = complete(client, headers[0])
    record(client, headers[0], application_id, "SUBMITTED")
    with Session(app.state.engine) as session:
        traveler_id = session.get(Application, application_id).traveler_id
        context = application_context(session, traveler_id, application_id)
        assert "recorded submitting" in context
        with pytest.raises(flow.InvalidAnswer):
            application_proposal(session, traveler_id, application_id, "change_answer",
                                 {"question_id": "occupation", "value": "unemployed"})


def test_archived_and_other_travelers_applications_are_refused(api):
    client, _, headers, _ = api
    application_id = complete(client, headers[0])
    assert record(client, headers[1], application_id, "SUBMITTED").status_code == 404
    assert record(client, headers[0], "f" * 32, "SUBMITTED").status_code == 404
    client.post(f"/applications/{application_id}/archive", headers=headers[0])
    assert record(client, headers[0], application_id, "SUBMITTED").status_code == 409
    client.post(f"/applications/{application_id}/unarchive", headers=headers[0])
    assert record(client, headers[0], application_id, "SUBMITTED").status_code == 200
    # Archiving a recorded application is still allowed, to tidy the dashboard.
    assert client.post(f"/applications/{application_id}/archive", headers=headers[0]).status_code == 200


@pytest.mark.parametrize("body", [{}, {"status": "DRAFT"}, {"status": "submitted"}, {"status": "SUBMITTED", "at": "x"}])
def test_decision_bodies_are_strict(api, body):
    client, _, headers, _ = api
    application_id = complete(client, headers[0])
    assert client.post(f"/applications/{application_id}/decision", headers=headers[0], json=body).status_code == 422


def test_deleting_a_recorded_application_removes_it(api):
    client, app, headers, _ = api
    application_id = complete(client, headers[0])
    record(client, headers[0], application_id, "SUBMITTED")
    assert client.delete(f"/applications/{application_id}", headers=headers[0]).status_code == 204
    with Session(app.state.engine) as session:
        assert session.get(Application, application_id) is None


def test_cross_site_decisions_are_rejected(api):
    client, _, headers, _ = api
    application_id = complete(client, headers[0])
    response = client.post(f"/applications/{application_id}/decision", json={"status": "SUBMITTED"},
                           headers={**headers[0], "Origin": "https://evil.example"})
    assert response.status_code == 403
