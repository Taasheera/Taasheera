"""Usage and API effectiveness against the KPIs (CMPS271-140), on seeded synthetic events."""

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlmodel import Session

from app.agent.filler import FORM_CHANGED
from app.auth import access_token
from app.kpis import compute
from app.main import create_app
from app.models import ActivityEvent, AgentRun, Application, ApplicationAnswer, AuthSession, Traveler

RANGE = {"start": "2026-11-01", "end": "2026-11-14"}
NAMES = {"t1": "Maya Haddad", "t2": "Omar Khalil", "t3": "Rana Saad", "staff": "Adam Admin", "support": "Sam Support"}
SECRET_ANSWER = "Zenith Hospital, Utopia"


def at(day: int, hour: int = 10, month: int = 11) -> datetime:
    return datetime(2026, month, day, hour, tzinfo=timezone.utc)


@pytest.fixture
def api(tmp_path):
    app = create_app(f"sqlite:///{(tmp_path / 'kpis.db').as_posix()}")
    with TestClient(app) as client:
        ids, headers = {}, {}
        with Session(app.state.engine) as session:
            for number, (key, name) in enumerate(NAMES.items(), start=1):
                role = {"staff": "admin", "support": "support"}.get(key, "traveler")
                traveler = Traveler(name=name, email=f"{key}@example.com", password_hash="test-hash", role=role)
                session.add(traveler)
                session.flush()
                ids[key] = traveler.id
                auth = AuthSession(id=f"kpi-session-{number}", traveler_id=traveler.id, refresh_hash=f"{number}" * 64,
                                   expires_at=2**31)
                session.add(auth)
                headers[key] = {"Authorization": f"Bearer {access_token(auth, app.state.auth_settings).access_token}"}
            session.commit()
        yield SimpleNamespace(client=client, app=app, ids=ids, headers=headers)


def seed(api) -> list[str]:
    """Synthetic activity, applications and agent runs; returns the ids that must never appear."""
    ids = api.ids
    events = [
        ("t1", "registration", "email_password", "success", at(1)),
        ("t1", "sign_in", "email_password", "success", at(1, 11)),
        ("t1", "sign_in", "email_password", "success", at(3)),
        ("t1", "page_visit", "dashboard", "visited", at(3, 12)),
        ("t1", "assistant_message", "assistant", "success", at(3, 13)),
        ("t1", "assistant_message", "assistant", "unavailable", at(4)),
        ("t2", "registration", "google", "success", at(2)),
        ("t2", "sign_in", "email_password", "failure", at(2, 9)),
        ("t2", "sign_in", "google", "success", at(2, 11)),
        ("t2", "page_visit", "apply", "visited", at(9)),
        ("t2", "api_error", "/applications/{application_id}", "error", at(9, 12)),
        ("t3", "sign_in", "email_password", "failure", at(5)),
        ("t3", "role_change", f"admin:{ids['staff']}:traveler->traveler", "success", at(6)),
        ("t3", "sign_in", "email_password", "suspended", at(7)),
        ("staff", "sign_in", "email_password", "success", at(3)),
        ("staff", "page_visit", "dashboard", "visited", at(3, 14)),
        ("staff", "api_error", "/admin/monitoring", "error", at(4, 8)),
        ("t1", "page_visit", "home", "visited", datetime(2026, 10, 31, 23, 59, tzinfo=timezone.utc)),
        ("t1", "page_visit", "home", "visited", at(15, 0)),
    ]
    applications = [
        ("a1", "t1", "schengen-short-stay", "ANSWERS_COMPLETE", at(2, 9), at(2, 15)),
        ("a2", "t1", "uk-standard-visitor", "SUBMITTED", at(3, 8), at(4, 8)),
        ("a3", "t2", "schengen-short-stay", "APPROVED", at(5, 8), at(5, 10)),
        ("a4", "t2", "schengen-short-stay", "DRAFT", at(6), at(6, 11)),
        ("a5", "t3", "us-b2-tourist", "INELIGIBLE", at(7), at(7, 11)),
        ("a6", "staff", "schengen-short-stay", "ANSWERS_COMPLETE", at(3), at(3, 11)),
        ("a7", "t1", "schengen-short-stay", "SUBMITTED", at(20, month=10), at(21, month=10)),
    ]
    runs = [
        ("a1", "ready", "The form is filled and waiting for you to review and submit it."),
        ("a1", "stopped", f"{FORM_CHANGED}: page 2"),
        ("a3", "stopped", "a CAPTCHA needs you"),
        ("a3", "failed", "the site did not load"),
        ("a4", "cancelled", "You cancelled this run."),
        ("a4", "queued", None),
        ("a6", "ready", "The form is filled and waiting for you to review and submit it."),
    ]
    secrets = []
    with Session(api.app.state.engine) as session:
        for key, event_type, identifier, outcome, when in events:
            session.add(ActivityEvent(traveler_id=ids[key], event_type=event_type, identifier=identifier,
                                      outcome=outcome, occurred_at=when))
        for key, owner, questionnaire_id, status, created, done in applications:
            application_id = f"{key}{'0' * 30}"
            secrets.append(application_id)
            session.add(Application(id=application_id, traveler_id=ids[owner], questionnaire_id=questionnaire_id,
                                    questionnaire_version=1, destination="X", visa_type="x", status=status,
                                    created_at=created, updated_at=done))
            session.flush()
            session.add(ApplicationAnswer(application_id=application_id, question_id="place_of_birth",
                                          value=SECRET_ANSWER, source="traveler", answered_at=created + timedelta(minutes=5)))
            session.add(ApplicationAnswer(application_id=application_id, question_id="purpose", value="tourism",
                                          source="traveler", answered_at=done))
        for number, (key, status, message) in enumerate(runs):
            run_id = f"run{number}".ljust(32, "0")
            secrets.append(run_id)
            owner = next(owner for name, owner, *_ in applications if name == key)
            session.add(AgentRun(id=run_id, traveler_id=ids[owner], application_id=f"{key}{'0' * 30}",
                                 site="schengen-harmonised", status=status, message=message, created_at=at(8)))
        session.commit()
    return secrets


def test_kpis_from_seeded_events(api):
    seed(api)
    response = api.client.get("/admin/kpis", params=RANGE, headers=api.headers["staff"])
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    kpis = response.json()
    assert (kpis["start"], kpis["end"], kpis["days"]) == ("2026-11-01", "2026-11-14", 14)

    usage = kpis["usage"]
    daily = {item["day"]: item["count"] for item in usage["daily_active"]}
    assert len(daily) == 14 and sum(daily.values()) == 5
    assert {day: count for day, count in daily.items() if count} == {
        "2026-11-01": 1, "2026-11-02": 1, "2026-11-03": 1, "2026-11-04": 1, "2026-11-09": 1}
    assert usage["weekly_active"] == [{"week_start": "2026-10-26", "count": 1}, {"week_start": "2026-11-02", "count": 2},
                                      {"week_start": "2026-11-09", "count": 1}]
    assert usage["active_users"] == 2 and usage["average_daily_active"] == round(5 / 14, 2)
    assert (usage["registrations"], usage["registrations_by_method"]) == (2, {"email_password": 1, "google": 1})
    assert (usage["sign_ins"], usage["sign_in_successes"], usage["sign_in_failures"], usage["sign_ins_suspended"]) == (6, 3, 2, 1)
    assert (usage["sign_in_success_rate"], usage["sign_in_failure_rate"]) == (0.5, 0.3333)

    applications = kpis["applications"]
    assert [(stage["stage"], stage["count"], stage["conversion"]) for stage in applications["funnel"]] == [
        ("started", 5, None), ("completed", 3, 0.6), ("submitted", 2, 0.6667), ("decided", 1, 0.5)]
    assert (applications["approved"], applications["refused"], applications["ineligible"]) == (1, 0, 1)
    assert applications["median_hours_to_complete"] == 6.0

    assert kpis["agent"] == {"runs": 6, "ready": 1, "stopped": 2, "site_changed": 1, "failed": 1, "cancelled": 1,
                             "in_progress": 1, "success_rate": 0.25, "failure_rate": 0.25, "site_changed_rate": 0.25}
    assert kpis["assistant"] == {"messages": 2, "answered": 1, "proposals": 0, "drafts": 0, "unavailable": 1,
                                 "unavailable_rate": 0.5, "users": 1}
    assert kpis["api_errors"] == 2
    assert {item["day"]: item["count"] for item in kpis["api_errors_daily"] if item["count"]} == {
        "2026-11-04": 1, "2026-11-09": 1}

    destinations = {item["questionnaire_id"]: item for item in kpis["destinations"]}
    assert {key: value for key, value in destinations["schengen-short-stay"].items() if key not in {"questionnaire_id", "title"}} == {
        "started": 3, "completed": 2, "submitted": 1, "decided": 1, "agent_runs": 6, "agent_ready": 1}
    assert (destinations["uk-standard-visitor"]["started"], destinations["uk-standard-visitor"]["submitted"]) == (1, 1)
    assert destinations["canada-visitor"]["started"] == 0


def test_kpis_hold_no_personal_data(api):
    secrets = seed(api)
    text = api.client.get("/admin/kpis", params=RANGE, headers=api.headers["staff"]).text
    for value in [*NAMES.values(), *(f"{key}@example.com" for key in NAMES), SECRET_ANSWER, "tourism",
                  "/applications/{application_id}", "CAPTCHA", *secrets]:
        assert value not in text


def test_kpis_use_a_fixed_number_of_grouped_queries(api):
    seed(api)
    statements = []

    def listener(connection, cursor, statement, *rest):
        statements.append(statement)

    event.listen(api.app.state.engine, "before_cursor_execute", listener)
    try:
        with Session(api.app.state.engine) as session:
            compute(session, date(2026, 11, 1), date(2026, 11, 14))
    finally:
        event.remove(api.app.state.engine, "before_cursor_execute", listener)
    assert len(statements) <= 12
    assert sum("GROUP BY" in statement for statement in statements) >= 7


@pytest.mark.parametrize("params", [
    {"start": "2026-11-14", "end": "2026-11-01"},
    {"start": "2026-01-01", "end": "2026-06-30"},
    {"start": "2026-11-01", "until": "2026-11-14"},
    {"start": "yesterday"},
])
def test_ranges_are_bounded(api, params):
    assert api.client.get("/admin/kpis", params=params, headers=api.headers["staff"]).status_code == 422


def test_the_default_range_is_the_last_thirty_days(api):
    kpis = api.client.get("/admin/kpis", headers=api.headers["staff"]).json()
    today = datetime.now(timezone.utc).date()
    assert (kpis["end"], kpis["days"]) == (today.isoformat(), 30)
    longest = {"start": (today - timedelta(days=179)).isoformat(), "end": today.isoformat()}
    assert api.client.get("/admin/kpis", params=longest, headers=api.headers["staff"]).json()["days"] == 180


@pytest.mark.parametrize("who", ["support", "t1"])
def test_only_admins_see_kpis(api, who):
    response = api.client.get("/admin/kpis", params=RANGE, headers=api.headers[who])
    assert response.status_code == 403 and response.json() == {"detail": "Admin access required."}
    assert api.client.get("/admin/kpis", params=RANGE).status_code == 401


def test_with_the_flag_off_the_kpis_are_hidden(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATURE_KPI_DASHBOARD", "false")
    with TestClient(create_app(f"sqlite:///{(tmp_path / 'off.db').as_posix()}")) as client:
        assert client.get("/admin/kpis").status_code == 404
        assert client.get("/features").json()["kpi_dashboard"] is False
