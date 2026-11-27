"""Support access with the traveler's consent (CMPS271-128) and talking to a person (CMPS271-92)."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.auth import access_token
from app.chat_history import save_turn
from app.main import create_app
from app.models import (ActivityEvent, AuthSession, DocumentAccess, FaqEntry, Notification, SupportAccessGrant,
                        SupportRequest, Traveler)
from app.notifications import SUPPORT_MESSAGES
from app.privacy import erase_account
from app.support_requests import MESSAGE_LIMIT, SupportRequestError, create_support_request
from test_applications_api import answer_all, start

PEOPLE = {"owner": "traveler", "other": "traveler", "support": "support", "admin": "admin"}


@pytest.fixture
def api(tmp_path):
    app = create_app(f"sqlite:///{(tmp_path / 'support.db').as_posix()}")
    with TestClient(app) as client:
        ids, headers = {}, {}
        with Session(app.state.engine) as session:
            for name, role in PEOPLE.items():
                traveler = Traveler(name=name.title(), email=f"{name}@example.com", password_hash="test-hash", role=role)
                session.add(traveler)
                session.flush()
                auth_session = AuthSession(id=f"support-session-{name}", traveler_id=traveler.id,
                                           refresh_hash=name.ljust(64, "0"), expires_at=2**31)
                session.add(auth_session)
                ids[name] = traveler.id
                headers[name] = {"Authorization": f"Bearer {access_token(auth_session, app.state.auth_settings).access_token}"}
            session.commit()
        application_id = start(client, headers["owner"])["id"]
        answer_all(client, headers["owner"], application_id, stop_at="place_of_birth")
        yield SimpleNamespace(client=client, app=app, ids=ids, headers=headers, application_id=application_id)


def share(api, hours=24, who="owner", application_id=None):
    return api.client.post("/support/access", headers=api.headers[who],
                           json={"application_id": application_id or api.application_id, "hours": hours})


def staff_view(api, who="support", application_id=None):
    return api.client.get(f"/admin/support/applications/{application_id or api.application_id}", headers=api.headers[who])


def ask(api, who="owner", **body):
    return api.client.post("/support/requests", headers=api.headers[who], json={"message": "I need help.", **body})


def rows(api, model, *conditions):
    with Session(api.app.state.engine) as session:
        return session.exec(select(model).where(*conditions)).all()


def test_staff_read_a_shared_application_only_after_the_traveler_grants_it(api):
    assert staff_view(api).status_code == 404
    granted = share(api, hours=24)
    assert granted.status_code == 201
    expires = datetime.fromisoformat(granted.json()["expires_at"])
    assert timedelta(hours=23, minutes=59) < expires - datetime.now(timezone.utc) <= timedelta(hours=24)
    assert granted.json()["title"] == "Schengen short-stay visa (type C)"

    for who in ("support", "admin"):
        response = staff_view(api, who)
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
    view = response.json()
    assert set(view) == {"application", "traveler_name", "access_expires_at", "answers", "check", "timeline"}
    assert view["traveler_name"] == "Owner"
    answers = {answer["question_id"]: answer for answer in view["answers"]}
    assert answers["purpose"]["value"] == "tourism" and answers["purpose"]["step"]
    assert view["check"]["ready"] is False and any(issue["kind"] == "missing" for issue in view["check"]["issues"])
    assert view["timeline"]["events"][0]["kind"] == "started"
    assert view["application"]["status"] == "DRAFT"


def test_every_staff_view_is_audited_and_shown_to_the_traveler_as_support(api):
    share(api)
    staff_view(api, "support")
    staff_view(api, "admin")
    audit = rows(api, DocumentAccess, DocumentAccess.purpose == "support_view")
    assert [(row.traveler_id, row.actor_id, row.upload_id) for row in audit] == [
        (api.ids["owner"], api.ids["support"], api.application_id), (api.ids["owner"], api.ids["admin"], api.application_id)]
    assert len(rows(api, ActivityEvent, ActivityEvent.traveler_id == api.ids["owner"],
                    ActivityEvent.event_type == "document_access", ActivityEvent.identifier == "support")) == 2

    history = api.client.get("/activity/documents", headers=api.headers["owner"])
    assert history.status_code == 200
    items = [item for item in history.json()["items"] if item["purpose"] == "support_view"]
    assert [(item["accessor"], item["actor_id"]) for item in items] == [("Support", None), ("Support", None)]
    assert all(item["upload_id"] == api.application_id for item in items)
    for staff in ("support@example.com", "admin@example.com"):
        assert staff not in history.text


def test_a_failed_audit_record_means_no_view(api, monkeypatch):
    share(api)
    monkeypatch.setattr("app.support.record_activity", lambda *args: None)
    assert staff_view(api).status_code == 404


def test_expiry_and_revocation_end_access_at_once(api):
    grant = share(api).json()
    assert staff_view(api).status_code == 200
    with Session(api.app.state.engine) as session:
        row = session.get(SupportAccessGrant, grant["id"])
        row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        session.add(row)
        session.commit()
    assert staff_view(api).status_code == 404
    assert api.client.get("/support/access", headers=api.headers["owner"]).json() == []

    grant = share(api, hours=168).json()
    assert staff_view(api).status_code == 200
    assert [item["id"] for item in api.client.get("/support/access", headers=api.headers["owner"]).json()] == [grant["id"]]
    assert api.client.delete(f"/support/access/{grant['id']}", headers=api.headers["owner"]).status_code == 204
    assert staff_view(api).status_code == 404
    assert api.client.get("/support/access", headers=api.headers["owner"]).json() == []
    assert api.client.get("/admin/support/shared", headers=api.headers["support"]).json() == []
    assert api.client.delete(f"/support/access/{grant['id']}", headers=api.headers["owner"]).status_code == 204
    events = rows(api, ActivityEvent, ActivityEvent.traveler_id == api.ids["owner"], ActivityEvent.event_type == "support_access")
    assert [event.identifier for event in events] == ["granted", "granted", "revoked", "revoked"]


def test_granting_again_replaces_the_running_grant(api):
    first = share(api, hours=24).json()
    second = share(api, hours=72).json()
    assert [item["id"] for item in api.client.get("/support/access", headers=api.headers["owner"]).json()] == [second["id"]]
    revoked = rows(api, SupportAccessGrant, SupportAccessGrant.id == first["id"])[0]
    assert revoked.revoked_at is not None
    shared = api.client.get("/admin/support/shared", headers=api.headers["support"]).json()
    assert [(item["application_id"], item["traveler_name"]) for item in shared] == [(api.application_id, "Owner")]


def test_only_the_owner_grants_or_revokes_and_only_for_listed_durations(api):
    assert share(api, who="other").status_code == 404
    assert share(api, application_id="f" * 32).status_code == 404
    for hours in (0, 1, 48, 169, "72h"):
        assert share(api, hours=hours).status_code == 422
    assert api.client.post("/support/access", headers=api.headers["owner"],
                           json={"application_id": api.application_id, "hours": 24, "staff_id": 1}).status_code == 422
    grant = share(api).json()
    assert api.client.delete(f"/support/access/{grant['id']}", headers=api.headers["other"]).status_code == 404
    assert staff_view(api).status_code == 200
    assert api.client.get("/support/access", headers=api.headers["other"]).json() == []


def test_staff_without_a_grant_cannot_tell_an_unshared_application_from_a_missing_one(api):
    unshared, missing = staff_view(api), staff_view(api, application_id="f" * 32)
    assert unshared.status_code == missing.status_code == 404
    assert unshared.json() == missing.json() == {"detail": "Application not found."}
    other = start(api.client, api.headers["other"])["id"]
    share(api)
    assert staff_view(api, application_id=other).status_code == 404
    assert rows(api, DocumentAccess, DocumentAccess.purpose == "support_view") == []


@pytest.mark.parametrize("path", ["/admin/support/me", "/admin/support/requests", "/admin/support/shared",
                                  "/admin/support/requests/1", "/admin/support/applications/" + "a" * 32])
def test_travelers_cannot_reach_staff_routes(api, path):
    share(api)
    assert api.client.get(path, headers=api.headers["owner"]).status_code == 403
    assert api.client.get(path).status_code == 401


def test_staff_me_reports_the_role_for_support_and_admin(api):
    for who in ("support", "admin"):
        response = api.client.get("/admin/support/me", headers=api.headers[who])
        assert response.status_code == 200
        assert response.json() == {"id": api.ids[who], "name": who.title(), "email": f"{who}@example.com", "role": PEOPLE[who]}
    assert api.client.get("/admin/me", headers=api.headers["support"]).status_code == 403


def test_a_request_lifecycle_from_assistant_to_resolved(api):
    with Session(api.app.state.engine) as session:
        save_turn(session, api.ids["owner"], api.application_id, "My passport number is L898902C3", "Thanks, noted.")
    created = ask(api, message="  Which passport should I use?  ", application_id=api.application_id, share_hours=72)
    assert created.status_code == 201, created.text
    request = created.json()
    assert request["message"] == "Which passport should I use?"
    assert (request["status"], request["reply"], request["source"]) == ("open", None, "website")
    assert set(request) == {"id", "application_id", "source", "message", "status", "reply", "created_at", "updated_at",
                            "replied_at", "resolved_at"}
    [stored] = rows(api, SupportRequest)
    assert "L898902C3" not in stored.message and "Thanks" not in stored.message
    [grant] = api.client.get("/support/access", headers=api.headers["owner"]).json()
    assert timedelta(hours=71) < datetime.fromisoformat(grant["expires_at"]) - datetime.now(timezone.utc) <= timedelta(hours=72)

    support, admin = api.headers["support"], api.headers["admin"]
    page = api.client.get("/admin/support/requests", headers=support).json()
    assert page["total"] == 1
    [item] = page["items"]
    assert (item["traveler_name"], item["excerpt"], item["assigned_to_me"], item["assignee_name"]) == (
        "Owner", "Which passport should I use?", False, None)
    assert "owner@example.com" not in api.client.get("/admin/support/requests", headers=support).text
    assert api.client.get("/admin/support/requests?status=resolved", headers=support).json()["total"] == 0
    detail = api.client.get(f"/admin/support/requests/{item['id']}", headers=support).json()
    assert detail["access_expires_at"] is not None and detail["message"] == "Which passport should I use?"

    assigned = api.client.post(f"/admin/support/requests/{item['id']}/assign", headers=support).json()
    assert (assigned["status"], assigned["assigned_to_me"], assigned["assignee_name"]) == ("in_progress", True, "Support")
    seen_by_admin = api.client.get(f"/admin/support/requests/{item['id']}", headers=admin).json()
    assert (seen_by_admin["assigned_to_me"], seen_by_admin["assignee_name"]) == (False, "Support")
    assert api.client.get("/admin/support/requests?mine=true", headers=support).json()["total"] == 1
    assert api.client.get("/admin/support/requests?mine=true", headers=admin).json()["total"] == 0

    replied = api.client.put(f"/admin/support/requests/{item['id']}/reply", headers=support,
                             json={"reply": "Use the passport you will travel on."})
    assert replied.status_code == 200 and replied.json()["replied_at"]
    noted = api.client.put(f"/admin/support/requests/{item['id']}/notes", headers=support,
                           json={"notes": "Explained which passport counts."})
    assert noted.json()["notes"] == "Explained which passport counts."

    mine = api.client.get("/support/requests", headers=api.headers["owner"]).json()
    assert [(row["reply"], row["status"]) for row in mine] == [("Use the passport you will travel on.", "in_progress")]
    assert "notes" not in mine[0] and "Explained" not in api.client.get("/support/requests", headers=api.headers["owner"]).text

    resolved = api.client.put(f"/admin/support/requests/{item['id']}/status", headers=admin, json={"status": "resolved"})
    assert resolved.json()["status"] == "resolved" and resolved.json()["resolved_at"]
    again = api.client.put(f"/admin/support/requests/{item['id']}/status", headers=admin, json={"status": "resolved"})
    assert again.json()["resolved_at"] == resolved.json()["resolved_at"]
    notifications = rows(api, Notification, Notification.traveler_id == api.ids["owner"],
                         Notification.kind.in_(["support_replied", "support_resolved"]))
    assert [(row.kind, row.message, row.link) for row in notifications] == [
        ("support_replied", SUPPORT_MESSAGES["support_replied"], "/account#support-requests"),
        ("support_resolved", SUPPORT_MESSAGES["support_resolved"], "/account#support-requests")]
    reopened = api.client.put(f"/admin/support/requests/{item['id']}/status", headers=support, json={"status": "open"})
    assert (reopened.json()["status"], reopened.json()["resolved_at"]) == ("open", None)
    assert api.client.get("/notifications", headers=api.headers["owner"]).json()[0]["kind"] == "support_resolved"


def test_staff_replies_and_status_changes_are_checked(api):
    request_id = ask(api).json()["id"]
    support = api.headers["support"]
    assert api.client.put(f"/admin/support/requests/{request_id}/reply", headers=support, json={"reply": "   "}).status_code == 422
    assert api.client.put(f"/admin/support/requests/{request_id}/reply", headers=support, json={"reply": "x" * 2001}).status_code == 422
    assert api.client.put(f"/admin/support/requests/{request_id}/reply", headers=support, json={"reply": "Hi\x00"}).status_code == 422
    assert api.client.put(f"/admin/support/requests/{request_id}/status", headers=support, json={"status": "closed"}).status_code == 422
    assert api.client.post("/admin/support/requests/999/assign", headers=support).status_code == 404
    assert api.client.put(f"/admin/support/requests/{request_id}/notes", headers=support, json={"notes": ""}).json()["notes"] is None
    replied = api.client.put(f"/admin/support/requests/{request_id}/reply", headers=support, json={"reply": "Hello"}).json()
    assert (replied["status"], replied["assigned_to_me"]) == ("in_progress", True)


def test_travelers_see_only_their_own_requests_and_name_only_their_own_applications(api):
    ask(api, message="Mine")
    assert api.client.get("/support/requests", headers=api.headers["other"]).json() == []
    assert ask(api, who="other", application_id=api.application_id).status_code == 404
    assert ask(api, share_hours=24).status_code == 422
    assert ask(api, message="").status_code == 422
    assert ask(api, message="x" * (MESSAGE_LIMIT + 1)).status_code == 422
    assert ask(api, message="   ").status_code == 422
    assert ask(api, status="resolved").status_code == 422
    assert ask(api, source="telegram").status_code == 422
    assert [row["source"] for row in api.client.get("/support/requests", headers=api.headers["owner"]).json()] == ["website"]


def test_too_many_requests_are_refused(api):
    for _ in range(5):
        assert ask(api).status_code == 201
    assert ask(api).status_code == 429


def test_the_service_opens_requests_for_telegram_and_refuses_bad_input(api):
    owner, other = api.ids["owner"], api.ids["other"]
    with Session(api.app.state.engine) as session:
        created = create_support_request(session, owner, source="telegram", message="Help from Telegram\r\nPlease",
                                         application_id=api.application_id)
        assert created.id is not None
        session.commit()
        for kwargs in ({"source": "email", "message": "Hi"}, {"source": "telegram", "message": ""},
                       {"source": "telegram", "message": "x" * (MESSAGE_LIMIT + 1)},
                       {"source": "telegram", "message": "bad\x07bell"},
                       {"source": "telegram", "message": "Hi", "application_id": "f" * 32}):
            with pytest.raises(SupportRequestError):
                create_support_request(session, owner, **kwargs)
        with pytest.raises(SupportRequestError):
            create_support_request(session, other, source="telegram", message="Hi", application_id=api.application_id)
    [listed] = api.client.get("/support/requests", headers=api.headers["owner"]).json()
    assert (listed["source"], listed["message"], listed["application_id"]) == ("telegram", "Help from Telegram\nPlease",
                                                                              api.application_id)
    assert api.client.get("/admin/support/requests", headers=api.headers["support"]).json()["items"][0]["source"] == "telegram"


def test_deleting_an_application_ends_its_grants_and_keeps_the_request(api):
    ask(api, application_id=api.application_id, share_hours=24)
    assert api.client.delete(f"/applications/{api.application_id}", headers=api.headers["owner"]).status_code == 204
    assert rows(api, SupportAccessGrant) == []
    [request] = rows(api, SupportRequest)
    assert request.application_id is None
    assert staff_view(api).status_code == 404


def test_erasing_a_staff_account_keeps_their_work_but_not_their_link(api):
    request_id = ask(api).json()["id"]
    api.client.put(f"/admin/support/requests/{request_id}/reply", headers=api.headers["support"], json={"reply": "Done."})
    with Session(api.app.state.engine) as session:
        session.add(FaqEntry(question="Q?", answer="A.", category="General", published=True, updated_by=api.ids["support"]))
        session.commit()
        assert erase_account(session, session.get(Traveler, api.ids["support"]), api.app.state.passport_storage,
                             api.app.state.activity_directory, api.app.state.auth_settings.jwt_secret)
    [request] = rows(api, SupportRequest)
    assert (request.reply, request.assignee_id) == ("Done.", None)
    [entry] = rows(api, FaqEntry)
    assert (entry.question, entry.updated_by) == ("Q?", None)


def test_erasing_the_traveler_removes_their_requests_and_grants(api):
    ask(api, application_id=api.application_id, share_hours=24)
    with Session(api.app.state.engine) as session:
        assert erase_account(session, session.get(Traveler, api.ids["owner"]), api.app.state.passport_storage,
                             api.app.state.activity_directory, api.app.state.auth_settings.jwt_secret)
    assert rows(api, SupportRequest) == [] and rows(api, SupportAccessGrant) == []
    assert api.client.get("/admin/support/requests", headers=api.headers["support"]).json()["total"] == 0


def test_everything_is_hidden_while_the_flag_is_off(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATURE_SUPPORT_TOOLS", "false")
    app = create_app(f"sqlite:///{(tmp_path / 'off.db').as_posix()}")
    with TestClient(app) as client:
        with Session(app.state.engine) as session:
            admin = Traveler(name="Admin", email="admin@example.com", password_hash="test-hash", role="admin")
            session.add(admin)
            session.flush()
            auth_session = AuthSession(id="off-session", traveler_id=admin.id, refresh_hash="0" * 64, expires_at=2**31)
            session.add(auth_session)
            session.commit()
            headers = {"Authorization": f"Bearer {access_token(auth_session, app.state.auth_settings).access_token}"}
        for method, path in [("GET", "/support/requests"), ("POST", "/support/requests"), ("GET", "/support/access"),
                             ("GET", "/admin/support/me"), ("GET", "/admin/support/requests"),
                             ("GET", "/admin/help-content"), ("GET", "/help-content")]:
            assert client.request(method, path, headers=headers).status_code == 404, path
