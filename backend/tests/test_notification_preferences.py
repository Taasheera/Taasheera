"""Choose which notification emails to get (CMPS271-99)."""

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.main import create_app
from app.models import NotificationPreference, Traveler
from app.notification_jobs import send_pending_emails
from app.notification_preferences import CATEGORY_OF_KIND, category
from app.privacy import erase_account
from test_applications_api import api  # noqa: F401  (pytest fixture)
from test_notification_email import SITE, Outbox, add, stored

ALL_ON = {"application_updates": True, "reminders": True, "passport_expiry": True}


def save(client, headers, **switches):
    return client.put("/profile/notification-preferences", headers=headers, json={**ALL_ON, **switches})


def test_every_email_is_on_until_the_traveler_changes_it(api):  # noqa: F811
    client, app, headers, _ = api
    response = client.get("/profile/notification-preferences", headers=headers[0])
    assert response.status_code == 200 and response.json() == ALL_ON
    assert response.headers["cache-control"] == "private, no-store"
    with Session(app.state.engine) as session:
        assert session.exec(select(NotificationPreference)).all() == []


def test_saved_switches_are_returned_and_kept_per_traveler(api):  # noqa: F811
    client, _, headers, _ = api
    response = save(client, headers[0], reminders=False)
    assert response.status_code == 200
    assert response.json() == {**ALL_ON, "reminders": False}
    assert client.get("/profile/notification-preferences", headers=headers[0]).json() == {**ALL_ON, "reminders": False}
    assert client.get("/profile/notification-preferences", headers=headers[1]).json() == ALL_ON

    assert save(client, headers[0], application_updates=False).json() == {**ALL_ON, "application_updates": False}


@pytest.mark.parametrize("body", [
    {"application_updates": False},  # all three are required
    {**ALL_ON, "marketing": True},
    {**ALL_ON, "reminders": "maybe"},
])
def test_incomplete_or_unknown_switches_are_refused(api, body):  # noqa: F811
    client, _, headers, _ = api
    assert client.put("/profile/notification-preferences", headers=headers[0], json=body).status_code == 422


def test_signed_out_requests_and_other_sites_are_refused(api):  # noqa: F811
    client, _, headers, _ = api
    assert client.get("/profile/notification-preferences").status_code == 401
    other_site = client.put("/profile/notification-preferences", json=ALL_ON,
                            headers={**headers[0], "Origin": "https://attacker.example"})
    assert other_site.status_code == 403


def test_a_switched_off_kind_is_not_emailed_but_still_shown_in_the_app(api):  # noqa: F811
    client, app, headers, _ = api
    save(client, headers[0], application_updates=False)
    add(app, kind="status_changed")
    add(app, kind="application_stalled")
    add(app, traveler_id=2, kind="status_changed")
    outbox = Outbox()
    result = send_pending_emails(app.state.engine, outbox, SITE)
    assert (result.sent, result.skipped) == (2, 1)
    assert [row.email_status for row in stored(app)] == ["skipped", "sent", "sent"]
    # The bell still lists all of traveler 1's notifications.
    assert len(client.get("/notifications", headers=headers[0]).json()) == 2


def test_each_switch_covers_its_kinds():
    assert {category(kind) for kind in CATEGORY_OF_KIND} == set(ALL_ON)
    assert category("agent_finished") == "application_updates"
    assert category("application_stalled") == "reminders"
    assert category("passport_expiring") == "passport_expiry"
    assert category("anything_new") == "application_updates"


def test_every_email_says_where_to_change_the_settings(api):  # noqa: F811
    _, app, _, _ = api
    add(app)
    outbox = Outbox()
    send_pending_emails(app.state.engine, outbox, SITE)
    assert f"{SITE}/account" in outbox.sent[0][2]


def test_deleting_the_account_removes_the_settings(api):  # noqa: F811
    client, app, headers, _ = api
    save(client, headers[0], reminders=False)
    save(client, headers[1], reminders=False)
    with Session(app.state.engine) as session:
        assert erase_account(session, session.get(Traveler, 1), app.state.passport_storage,
                             app.state.activity_directory, app.state.auth_settings.jwt_secret)
    with Session(app.state.engine) as session:
        assert [row.traveler_id for row in session.exec(select(NotificationPreference)).all()] == [2]


def test_with_the_flag_off_the_settings_are_hidden(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATURE_EMAIL_NOTIFICATIONS", "false")
    app = create_app(f"sqlite:///{(tmp_path / 'off.db').as_posix()}")
    with TestClient(app) as client:
        client.post("/auth/register", json={"name": "Maya", "email": "maya@example.com",
                                            "password": "a long synthetic password"})
        token = client.post("/auth/login", json={"email": "maya@example.com",
                                                 "password": "a long synthetic password"}).json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        assert client.get("/profile/notification-preferences", headers=headers).status_code == 404
        assert client.get("/features").json()["email_notifications"] is False
