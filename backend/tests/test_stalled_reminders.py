"""Remind travelers about applications left unfinished (CMPS271-101)."""

from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from app.models import Application, Notification, Traveler
from app.notification_jobs import STALL_AFTER, remind_stalled_applications, run_notification_jobs
from test_applications_api import ANSWERS, answer_all, api, put, start  # noqa: F401  (api is a fixture)
from test_notification_email import SITE, Outbox

TITLE = "Schengen short-stay visa (type C)"


def touched(app, application_id, when):
    with Session(app.state.engine) as session:
        application = session.get(Application, application_id)
        application.updated_at = when
        session.add(application)
        session.commit()


def reminders(app) -> list[Notification]:
    with Session(app.state.engine) as session:
        return list(session.exec(select(Notification).where(Notification.kind == "application_stalled")
                                 .order_by(Notification.id)).all())


def draft(client, app, headers, days_ago: float):
    application_id = start(client, headers)["id"]
    touched(app, application_id, datetime.now(timezone.utc) - timedelta(days=days_ago))
    return application_id


def test_a_draft_untouched_for_a_week_gets_one_reminder_by_bell_and_email(api):  # noqa: F811
    client, app, headers, _ = api
    application_id = draft(client, app, headers[0], days_ago=8)
    outbox = Outbox()
    assert run_notification_jobs(app.state.engine, outbox, SITE).sent == 1

    [reminder] = reminders(app)
    assert (reminder.traveler_id, reminder.application_id) == (1, application_id)
    assert reminder.message == (f"Your {TITLE} application has been waiting for a week. "
                                "Open it to continue where you left off.")
    assert reminder.link == f"/dashboard/{application_id}"
    [(_, subject, body)] = outbox.sent
    assert subject == "New notification from Taasheera"
    assert f"{SITE}/dashboard" in body
    assert application_id not in body and TITLE not in body
    assert [item["kind"] for item in client.get("/notifications", headers=headers[0]).json()] == ["application_stalled"]

    # The next runs, even days later, add nothing while it stays untouched.
    for days in (0, 3, 20):
        assert remind_stalled_applications(app.state.engine, now=datetime.now(timezone.utc) + timedelta(days=days)) == 0
    assert len(reminders(app)) == 1


def test_a_draft_touched_within_the_week_is_left_alone(api):  # noqa: F811
    client, app, headers, _ = api
    draft(client, app, headers[0], days_ago=6.9)
    assert remind_stalled_applications(app.state.engine) == 0


def test_the_week_is_counted_from_the_last_change(api):  # noqa: F811
    client, app, headers, _ = api
    application_id = start(client, headers[0])["id"]
    now = datetime.now(timezone.utc)
    assert remind_stalled_applications(app.state.engine, now=now + STALL_AFTER - timedelta(minutes=1)) == 0
    assert remind_stalled_applications(app.state.engine, now=now + STALL_AFTER + timedelta(minutes=1)) == 1
    assert reminders(app)[0].application_id == application_id


def test_working_on_it_again_and_stalling_again_earns_a_new_reminder(api):  # noqa: F811
    client, app, headers, _ = api
    application_id = draft(client, app, headers[0], days_ago=8)
    assert remind_stalled_applications(app.state.engine) == 1

    # The traveler answers a question: that counts as working on it.
    current = client.get(f"/applications/{application_id}", headers=headers[0]).json()["current"]["id"]
    assert put(client, headers[0], application_id, current, ANSWERS[current]).status_code == 200
    assert remind_stalled_applications(app.state.engine) == 0
    later = datetime.now(timezone.utc) + STALL_AFTER + timedelta(hours=1)
    assert remind_stalled_applications(app.state.engine, now=later) == 1
    assert len(reminders(app)) == 2


def test_only_unfinished_active_drafts_are_reminded(api):  # noqa: F811
    client, app, headers, _ = api
    long_ago = datetime.now(timezone.utc) - timedelta(days=30)
    complete = start(client, headers[0])["id"]
    answer_all(client, headers[0], complete)
    archived = start(client, headers[0])["id"]
    assert client.post(f"/applications/{archived}/archive", headers=headers[0]).status_code in (200, 204)
    suspended_owner = start(client, headers[1])["id"]
    for application_id in (complete, archived, suspended_owner):
        touched(app, application_id, long_ago)
    with Session(app.state.engine) as session:
        assert session.get(Application, complete).status == "ANSWERS_COMPLETE"
        owner = session.get(Traveler, 2)
        owner.status = "suspended"
        session.add(owner)
        session.commit()
    assert remind_stalled_applications(app.state.engine) == 0


def test_with_reminder_emails_off_the_bell_still_reminds(api):  # noqa: F811
    client, app, headers, _ = api
    client.put("/profile/notification-preferences", headers=headers[0],
               json={"application_updates": True, "reminders": False, "passport_expiry": True})
    draft(client, app, headers[0], days_ago=10)
    outbox = Outbox()
    result = run_notification_jobs(app.state.engine, outbox, SITE)
    assert (result.sent, result.skipped) == (0, 1) and outbox.sent == []
    assert len(reminders(app)) == 1
