"""In-app notifications when an application changes (US 28, CMPS271-96)."""

from sqlmodel import Session, select

from app.models import Application, Notification, Traveler
from app.notifications import notify_agent_run, notify_status_change
from app.privacy import erase_account
from test_applications_api import ANSWERS, SPECIMEN, answer_all, api, put, start  # noqa: F401  (pytest fixture)

TITLE = "Schengen short-stay visa (type C)"


def listed(client, headers):
    response = client.get("/notifications", headers=headers)
    assert response.status_code == 200
    return response.json()


def stored(app) -> list[Notification]:
    with Session(app.state.engine) as session:
        return session.exec(select(Notification).order_by(Notification.id)).all()


def test_finishing_the_questions_notifies_the_owner_once(api):  # noqa: F811
    client, _, headers, _ = api
    application_id = start(client, headers[0])["id"]
    assert listed(client, headers[0]) == []  # starting an application is not a status change

    state = answer_all(client, headers[0], application_id)
    assert state["status"] == "ANSWERS_COMPLETE"
    # About twenty answers, but the status changed once, so there is one notification.
    [notification] = listed(client, headers[0])
    assert notification["kind"] == "status_changed"
    assert notification["message"] == f"Every question in your {TITLE} application is answered."
    assert notification["link"] == f"/dashboard/{application_id}"
    assert notification["application_id"] == application_id
    assert notification["read"] is False
    assert notification["created_at"].endswith(("Z", "+00:00"))


def test_a_stopping_answer_and_its_correction_each_notify(api):  # noqa: F811
    client, _, headers, _ = api
    application_id = start(client, headers[0])["id"]
    stopped = answer_all(client, headers[0], application_id, {**ANSWERS, "stay_over_90_days": True})
    assert stopped["status"] == "INELIGIBLE"
    assert put(client, headers[0], application_id, "stay_over_90_days", False).json()["status"] == "DRAFT"

    messages = [item["message"] for item in listed(client, headers[0])]
    assert messages == [  # newest first
        f"Your {TITLE} application has questions left to answer. Open it to continue.",
        f"One of your answers stops your {TITLE} application. Open it to see why.",
    ]


def test_travelers_only_see_their_own_notifications(api):  # noqa: F811
    client, _, headers, _ = api
    answer_all(client, headers[0], start(client, headers[0])["id"])
    response = client.get("/notifications", headers=headers[1])
    assert response.status_code == 200 and response.json() == []
    assert response.headers["cache-control"] == "no-store"
    assert client.get("/notifications").status_code == 401
    assert client.get("/notifications?limit=0", headers=headers[0]).status_code == 422
    assert client.get("/notifications?limit=101", headers=headers[0]).status_code == 422


def test_messages_never_contain_passport_or_answer_values(api):  # noqa: F811
    client, app, headers, _ = api
    application_id = start(client, headers[0])["id"]
    answer_all(client, headers[0], application_id)
    with Session(app.state.engine) as session:
        application = session.get(Application, application_id)
        notify_agent_run(session, application)
        notify_agent_run(session, application, "the site asked for a CAPTCHA")
        session.commit()
    text = " ".join(row.message for row in stored(app))
    private = [SPECIMEN["surname"], SPECIMEN["document_number"], SPECIMEN["birth_date"],
               ANSWERS["phone"], ANSWERS["host_details"], ANSWERS["arrival_date"]]
    assert not [value for value in private if value in text]


def test_recorded_decisions_and_agent_runs_have_their_own_messages(api):  # noqa: F811
    client, app, headers, _ = api
    application_id = start(client, headers[0])["id"]
    with Session(app.state.engine) as session:
        application = session.get(Application, application_id)
        for status in ("SUBMITTED", "APPROVED", "REFUSED", "SOMETHING_NEW"):
            previous, application.status = application.status, status
            assert notify_status_change(session, application, previous) is not None
        assert notify_status_change(session, application, application.status) is None  # unchanged
        notify_agent_run(session, application)
        notify_agent_run(session, application, "the site asked for a CAPTCHA")
        session.commit()

    assert [(item["kind"], item["message"]) for item in reversed(listed(client, headers[0]))] == [
        ("status_changed", f"You recorded that your {TITLE} application was submitted to the embassy."),
        ("status_changed", f"You recorded that your {TITLE} application was approved."),
        ("status_changed", f"You recorded that your {TITLE} application was refused."),
        ("status_changed", f"Your {TITLE} application has a new status."),
        ("agent_finished", f"The agent filled the embassy form for your {TITLE} application and stopped "
                           "before submitting. Review it before you submit."),
        ("agent_stopped", f"The agent stopped filling the embassy form for your {TITLE} application: "
                          "the site asked for a CAPTCHA."),
    ]


def test_deleting_an_application_removes_only_its_notifications(api):  # noqa: F811
    client, app, headers, _ = api
    doomed, kept = start(client, headers[0])["id"], start(client, headers[0])["id"]
    answer_all(client, headers[0], doomed)
    answer_all(client, headers[0], kept)
    assert len(listed(client, headers[0])) == 2

    assert client.delete(f"/applications/{doomed}", headers=headers[0]).status_code == 204
    assert [item["application_id"] for item in listed(client, headers[0])] == [kept]


def test_deleting_the_account_removes_every_notification(api):  # noqa: F811
    client, app, headers, storage = api
    answer_all(client, headers[0], start(client, headers[0])["id"])
    answer_all(client, headers[1], start(client, headers[1])["id"])
    with Session(app.state.engine) as session:
        traveler = session.get(Traveler, 1)
        assert erase_account(session, traveler, app.state.passport_storage, app.state.activity_directory,
                             app.state.auth_settings.jwt_secret)
    assert [row.traveler_id for row in stored(app)] == [2]


def test_the_bell_counts_unread_and_marks_one_or_all_read(api):  # noqa: F811
    client, app, headers, _ = api
    for owner in (0, 0, 1):
        answer_all(client, headers[owner], start(client, headers[owner])["id"])
    count = lambda who: client.get("/notifications/unread-count", headers=headers[who]).json()["unread"]
    assert (count(0), count(1)) == (2, 1)

    first, second = listed(client, headers[0])
    assert client.post(f"/notifications/{first['id']}/read", headers=headers[0]).status_code == 204
    assert client.post(f"/notifications/{first['id']}/read", headers=headers[0]).status_code == 204  # again: no change
    assert [item["read"] for item in listed(client, headers[0])] == [True, False]
    assert count(0) == 1

    assert client.post("/notifications/read-all", headers=headers[0]).status_code == 204
    assert (count(0), count(1)) == (0, 1)  # only the owner's
    assert all(item["read"] for item in listed(client, headers[0]))


def test_nobody_can_read_or_mark_another_travelers_notifications(api):  # noqa: F811
    client, app, headers, _ = api
    answer_all(client, headers[1], start(client, headers[1])["id"])
    [theirs] = listed(client, headers[1])
    assert client.post(f"/notifications/{theirs['id']}/read", headers=headers[0]).status_code == 404
    assert client.post("/notifications/999999/read", headers=headers[0]).status_code == 404
    assert client.post("/notifications/read-all", headers=headers[0]).status_code == 204
    assert listed(client, headers[1])[0]["read"] is False

    cross_site = {**headers[1], "Origin": "https://evil.example"}
    assert client.post(f"/notifications/{theirs['id']}/read", headers=cross_site).status_code == 403
    assert client.post("/notifications/read-all", headers=cross_site).status_code == 403
    assert client.get("/notifications/unread-count").status_code == 401
    assert client.post("/notifications/read-all").status_code in (401, 403)
    assert listed(client, headers[1])[0]["read"] is False
