"""Submitting it yourself: steps, appointment and reminders, checklist and the stage (CMPS271-72/73/77/79)."""

from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from app.applications.tracking import remind_appointments, submission_guides
from app.applications.visa_guides import load_guides
from app.models import ApplicationTracking, Notification
from test_applications_api import START, answer_all, api, start  # noqa: F401 (api is a fixture)


def ready(client, headers):
    application_id = start(client, headers)["id"]
    answer_all(client, headers, application_id)
    return application_id


def submission(client, headers, application_id):
    response = client.get(f"/applications/{application_id}/submission", headers=headers)
    assert response.status_code == 200 and response.headers["cache-control"] == "private, no-store"
    return response.json()


def book(client, headers, application_id, at, location="Visa centre, Beirut"):
    return client.put(f"/applications/{application_id}/appointment", headers=headers,
                      json={"at": at.isoformat(), "location": location})


def test_every_destination_has_a_guide_with_an_official_link():
    guides = submission_guides()
    assert set(guides) == {guide.id for guide in load_guides()}
    assert all(guide.booking_link.url.startswith("https://") for guide in guides.values())


def test_the_steps_tick_off_as_the_application_moves_on(api):
    client, _, headers, _ = api
    draft = start(client, headers[0])["id"]
    state = submission(client, headers[0], draft)
    assert state["stage"] == "Answering questions" and state["current"] == "answers"
    assert [step["id"] for step in state["steps"]] == ["answers", "form", "documents", "book", "hand_in", "decision"]
    book_step = next(step for step in state["steps"] if step["id"] == "book")
    assert "€90" in book_step["text"] and book_step["link"]["url"].startswith("https://")

    application_id = ready(client, headers[0])
    state = submission(client, headers[0], application_id)
    assert state["stage"] == "Preparing documents" and state["steps"][0]["done"] and state["current"] == "form"

    assert client.get(f"/applications/{application_id}/documents/packet", headers=headers[0]).status_code == 200
    assert submission(client, headers[0], application_id)["stage"] == "Ready to hand in"

    at = datetime.now(timezone.utc) + timedelta(days=20)
    booked = book(client, headers[0], application_id, at)
    assert booked.status_code == 200
    assert booked.json()["stage"] == "Appointment booked"
    assert booked.json()["appointment"]["location"] == "Visa centre, Beirut"
    assert next(step for step in booked.json()["steps"] if step["id"] == "book")["done"] is True

    timeline = client.get(f"/applications/{application_id}/timeline", headers=headers[0]).json()
    assert any(event["text"] == "Downloaded the print-ready packet" for event in timeline["events"])
    assert "appointment is booked" in timeline["next"]["text"]

    assert client.post(f"/applications/{application_id}/decision", headers=headers[0],
                       json={"record": "SUBMITTED"}).status_code == 200
    state = submission(client, headers[0], application_id)
    assert state["stage"] == "Handed in, waiting for the decision" and state["current"] == "decision"


def test_the_checklist_marks_uploads_and_what_to_bring(api):
    client, _, headers, _ = api
    application_id = ready(client, headers[0])
    items = {item["label"]: item for item in submission(client, headers[0], application_id)["checklist"]}
    assert items["Your passport"]["status"] == "bring"
    assert items["The application form, printed and signed"]["status"] == "bring"
    assert items["Travel medical insurance"]["status"] == "missing"
    assert items["Cover letter"]["optional"] is True and items["Cover letter"]["status"] == "bring"
    assert "Your appointment confirmation" in items


def test_appointments_are_validated_owner_only_and_can_be_removed(api):
    client, app, headers, _ = api
    application_id = ready(client, headers[0])
    soon = datetime.now(timezone.utc) + timedelta(days=3)
    assert book(client, headers[1], application_id, soon).status_code == 404
    assert client.get(f"/applications/{application_id}/submission", headers=headers[1]).status_code == 404
    assert book(client, headers[0], application_id, datetime.now(timezone.utc) - timedelta(days=1)).status_code == 422
    assert book(client, headers[0], application_id, datetime.now(timezone.utc) + timedelta(days=500)).status_code == 422
    naive = client.put(f"/applications/{application_id}/appointment", headers=headers[0],
                       json={"at": "2027-01-01T10:00:00"})
    assert naive.status_code == 422
    assert book(client, headers[0], application_id, soon).status_code == 200
    removed = client.delete(f"/applications/{application_id}/appointment", headers=headers[0])
    assert removed.status_code == 200 and removed.json()["appointment"]["at"] is None


def test_e_visas_have_no_appointment(api):
    client, _, headers, _ = api
    application_id = client.post("/applications", headers=headers[0],
                                 json={"destination": "TUR", "visa_type": "e_visa"}).json()["id"]
    state = submission(client, headers[0], application_id)
    assert state["appointment"]["needed"] is False
    assert any(item["label"] == "Your printed e-Visa" for item in state["checklist"])
    at = datetime.now(timezone.utc) + timedelta(days=5)
    assert book(client, headers[0], application_id, at).status_code == 409


def test_reminders_go_out_a_week_and_a_day_before_once_each(api):
    client, app, headers, _ = api
    application_id = ready(client, headers[0])
    now = datetime.now(timezone.utc)
    assert book(client, headers[0], application_id, now + timedelta(days=10)).status_code == 200
    engine = app.state.engine
    assert remind_appointments(engine, now=now) == 0
    assert remind_appointments(engine, now=now + timedelta(days=4)) == 1
    assert remind_appointments(engine, now=now + timedelta(days=4, hours=1)) == 0
    assert remind_appointments(engine, now=now + timedelta(days=9)) == 1
    assert remind_appointments(engine, now=now + timedelta(days=11)) == 0
    with Session(engine) as session:
        messages = [n.message for n in session.exec(select(Notification).where(
            Notification.kind == "appointment_reminder")).all()]
    assert len(messages) == 2 and "in a week" in messages[0] and "tomorrow" in messages[1]
    assert all(str(now.year) not in message for message in messages)


def test_moving_the_appointment_starts_its_reminders_again(api):
    client, app, headers, _ = api
    application_id = ready(client, headers[0])
    now = datetime.now(timezone.utc)
    book(client, headers[0], application_id, now + timedelta(days=5))
    assert remind_appointments(app.state.engine, now=now) == 1
    book(client, headers[0], application_id, now + timedelta(days=6))
    with Session(app.state.engine) as session:
        assert session.get(ApplicationTracking, application_id).appointment_reminded_days is None
    assert remind_appointments(app.state.engine, now=now) == 1


def test_deleting_the_application_removes_its_tracking(api):
    client, app, headers, _ = api
    application_id = ready(client, headers[0])
    book(client, headers[0], application_id, datetime.now(timezone.utc) + timedelta(days=5))
    assert client.delete(f"/applications/{application_id}", headers=headers[0]).status_code == 204
    with Session(app.state.engine) as session:
        assert session.exec(select(ApplicationTracking)).all() == []
    assert START


def test_watching_for_slots_notifies_once_per_new_slot(api, tmp_path, monkeypatch):
    import json
    from app.applications.tracking import check_appointment_slots

    feed = tmp_path / "slots.json"
    feed.write_text(json.dumps({"slots": {"schengen-short-stay": [{"centre": "Centre A", "in_days": 9}]}}), encoding="utf-8")
    monkeypatch.setenv("APPOINTMENT_SLOTS_FILE", str(feed))
    client, app, headers, _ = api
    application_id = ready(client, headers[0])
    state = submission(client, headers[0], application_id)
    assert state["slots"]["available"] is True and state["slots"]["watching"] is False
    assert state["slots"]["earliest"]["centre"] == "Centre A"

    watched = client.put(f"/applications/{application_id}/slot-watch", headers=headers[0], json={"watch": True})
    assert watched.status_code == 200 and watched.json()["slots"]["watching"] is True
    engine = app.state.engine
    assert check_appointment_slots(engine) == 1
    assert check_appointment_slots(engine) == 0  # the same slot isn't announced twice
    feed.write_text(json.dumps({"slots": {"schengen-short-stay": [{"centre": "Centre B", "in_days": 5}]}}), encoding="utf-8")
    assert check_appointment_slots(engine) == 1
    with Session(engine) as session:
        messages = [n.message for n in session.exec(select(Notification).where(Notification.kind == "appointment_slot"))]
    assert len(messages) == 2 and "Centre" not in messages[0]

    # Booking the appointment stops the watch from notifying.
    book(client, headers[0], application_id, datetime.now(timezone.utc) + timedelta(days=5))
    feed.write_text(json.dumps({"slots": {"schengen-short-stay": [{"centre": "Centre C", "in_days": 3}]}}), encoding="utf-8")
    assert check_appointment_slots(engine) == 0
    assert client.put(f"/applications/{application_id}/slot-watch", headers=headers[1], json={"watch": False}).status_code == 404


def test_slots_after_the_trip_or_for_e_visas_are_not_offered(api, tmp_path, monkeypatch):
    import json

    feed = tmp_path / "slots.json"
    feed.write_text(json.dumps({"slots": {"schengen-short-stay": [{"centre": "Late", "in_days": 400}],
                                          "turkiye-evisa": [{"centre": "None", "in_days": 3}]}}), encoding="utf-8")
    monkeypatch.setenv("APPOINTMENT_SLOTS_FILE", str(feed))
    client, _, headers, _ = api
    application_id = ready(client, headers[0])
    assert submission(client, headers[0], application_id)["slots"]["earliest"] is None
    turkey = client.post("/applications", headers=headers[0], json={"destination": "TUR", "visa_type": "e_visa"}).json()["id"]
    assert submission(client, headers[0], turkey)["slots"]["available"] is False
    assert client.put(f"/applications/{turkey}/slot-watch", headers=headers[0], json={"watch": True}).status_code == 409
