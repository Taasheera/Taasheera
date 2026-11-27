"""Sprint 5 infrastructure monitoring: Sentry releases/traces/job check-ins, request logs and the
operator health page. Everything sent or shown is metadata: no traveler data, IDs or URLs."""
import json
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

import bcrypt
import pytest
import sentry_sdk
from fastapi.testclient import TestClient
from sentry_sdk.transport import Transport
from sqlmodel import Session, select

from app.main import create_app
from app.models import AgentRun, Application, Notification, Traveler
from app.monitoring import configure_monitoring, deploy_environment, monitored_job, scrub_transaction
from app.observability import RequestStats, request_log

PASSWORD = "Synthetic-password-123"
HASH = bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt(rounds=4)).decode()
APPLICATION_ID = "0123456789abcdef0123456789abcdef"


class MemoryTransport(Transport):
    sent = []

    def capture_envelope(self, envelope):
        MemoryTransport.sent.append(envelope)


@pytest.fixture
def sentry_memory(monkeypatch):
    MemoryTransport.sent = []
    real_init = sentry_sdk.init
    monkeypatch.setattr(sentry_sdk, "init", lambda **kwargs: real_init(**kwargs, transport=MemoryTransport))
    monkeypatch.setenv("SENTRY_DSN", "https://public@sentry.example.com/1")
    yield MemoryTransport.sent
    client = sentry_sdk.get_client()
    if client.is_active():
        client.close()


def items(envelopes, kind):
    return [item for envelope in envelopes for item in envelope.items if item.type == kind]


def build(tmp_path, roles=("admin", "support", "traveler")):
    app = create_app(f"sqlite:///{tmp_path / 'infra.db'}")
    client = TestClient(app)
    client.__enter__()
    tokens = {}
    for role in roles:
        with Session(app.state.engine) as session:
            session.add(Traveler(name=f"Synthetic {role}", email=f"{role}@example.com", password_hash=HASH, role=role))
            session.commit()
        client.cookies.clear()
        tokens[role] = client.post("/auth/login", json={"email": f"{role}@example.com", "password": PASSWORD}).json()["access_token"]
    client.cookies.clear()
    return SimpleNamespace(app=app, client=client, headers=lambda role: {"Authorization": f"Bearer {tokens[role]}"})


@pytest.fixture
def api(tmp_path):
    built = build(tmp_path)
    yield built
    built.client.__exit__(None, None, None)


def test_transaction_scrubber_keeps_route_templates_timings_and_sql_only():
    source = {"event_id": "e", "type": "transaction", "transaction": "/applications/{application_id}",
              "transaction_info": {"source": "route"}, "timestamp": 2, "start_timestamp": 1, "release": "taasheera@abc",
              "environment": "staging", "user": {"email": "private@example.com"},
              "request": {"url": "https://site/applications/secret-id?token=secret", "headers": {"Cookie": "secret"}},
              "breadcrumbs": [{"message": "private"}], "extra": {"passport": "private"},
              "contexts": {"trace": {"trace_id": "t", "span_id": "s", "op": "http.server", "data": {"url": "secret"}},
                           "response": {"status_code": 404}},
              "tags": {"component": "api", "token": "secret"},
              "spans": [{"span_id": "a", "op": "db", "description": "SELECT application.id FROM application WHERE application.id = ?",
                         "data": {"db.params": ["secret"]}},
                        {"span_id": "b", "op": "http.client", "description": "GET https://gemini/secret"}]}
    result = scrub_transaction(source, {})
    assert result["transaction"] == "/applications/{application_id}"
    assert result["contexts"] == {"trace": {"trace_id": "t", "span_id": "s", "op": "http.server"},
                                  "response": {"status_code": 404}}
    assert result["tags"] == {"component": "api"}
    assert result["spans"][0]["description"].startswith("SELECT application.id")
    assert "description" not in result["spans"][1]
    assert not any(word in json.dumps(result) for word in ("private", "secret", "Cookie"))
    unmatched = scrub_transaction({**source, "transaction": "/applications/secret-id", "transaction_info": {"source": "url"}}, {})
    assert unmatched["transaction"] == "unmatched route" and "secret" not in json.dumps(unmatched)


def test_traces_name_routes_by_template_and_carry_release_and_environment(tmp_path, monkeypatch, sentry_memory):
    monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", "1")
    monkeypatch.setenv("SENTRY_RELEASE", "taasheera@0123456789abcdef")
    monkeypatch.setenv("DEPLOY_ENVIRONMENT", "staging")
    built = build(tmp_path, roles=("traveler",))
    try:
        assert built.client.get(f"/applications/{APPLICATION_ID}", headers=built.headers("traveler")).status_code == 404
        sentry_sdk.flush()
    finally:
        built.client.__exit__(None, None, None)
    transactions = [item.get_transaction_event() for item in items(sentry_memory, "transaction")]
    named = [event for event in transactions if event["transaction"] == "/applications/{application_id}"]
    assert named, [event["transaction"] for event in transactions]
    event = named[-1]
    assert event["release"] == "taasheera@0123456789abcdef" and event["environment"] == "staging"
    assert event["contexts"]["response"] == {"status_code": 404}
    assert any(span.get("op") == "db" for span in event["spans"])
    payload = json.dumps(transactions)
    assert APPLICATION_ID not in payload and "Bearer" not in payload and "traveler@example.com" not in payload


def test_monitoring_settings_are_validated(monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", "https://public@sentry.example.com/1")
    for name, value in (("SENTRY_TRACES_SAMPLE_RATE", "2"), ("SENTRY_TRACES_SAMPLE_RATE", "all"),
                        ("SENTRY_RELEASE", "taasheera release"), ("SENTRY_ENVIRONMENT", "Prod!")):
        with monkeypatch.context() as scoped:
            scoped.setenv(name, value)
            with pytest.raises(RuntimeError):
                configure_monitoring()
    monkeypatch.setenv("DEPLOY_ENVIRONMENT", "qa")
    with pytest.raises(RuntimeError):
        deploy_environment()
    monkeypatch.delenv("DEPLOY_ENVIRONMENT")
    monkeypatch.setenv("APP_ENV", "production")
    assert deploy_environment() == "production"


def test_job_runs_check_in_and_failures_report_only_the_job_name(sentry_memory):
    configure_monitoring(component="jobs")
    with monitored_job("notification-jobs", interval_seconds=900):
        pass
    with pytest.raises(ValueError):
        with monitored_job("notification-jobs", interval_seconds=900):
            raise ValueError("private passport TEST0001")
    sentry_sdk.flush()
    check_ins = [json.loads(item.payload.get_bytes()) for item in items(sentry_memory, "check_in")]
    assert [check["status"] for check in check_ins] == ["in_progress", "ok", "in_progress", "error"]
    assert check_ins[0]["monitor_config"]["schedule"] == {"type": "interval", "value": 15, "unit": "minute"}
    errors = [item.get_event() for item in items(sentry_memory, "event")]
    assert errors and errors[-1]["message"] == "Taasheera job error"
    assert errors[-1]["tags"]["job"] == "notification-jobs" and errors[-1]["fingerprint"][-1] == "notification-jobs"
    assert "TEST0001" not in json.dumps(errors) and "ValueError" not in json.dumps(errors)


def test_request_logs_carry_a_safe_request_id_and_database_time(api):
    lines = []

    class Capture(logging.Handler):
        def emit(self, record):
            lines.append(record.activity)

    handler = Capture()
    request_log.addHandler(handler)
    try:
        good = api.client.get("/applications", headers={**api.headers("traveler"), "X-Request-ID": "loadtest-run-42"})
        bad = api.client.get("/applications", headers={**api.headers("traveler"), "X-Request-ID": "bad id\r\ninjected"})
    finally:
        request_log.removeHandler(handler)
    assert good.headers["x-request-id"] == "loadtest-run-42"
    assert bad.headers["x-request-id"] != "bad id\r\ninjected" and len(bad.headers["x-request-id"]) == 32
    first = next(line for line in lines if line["request_id"] == "loadtest-run-42")
    assert first["route"] == "/applications" and first["status"] == 200
    assert first["db_queries"] >= 1 and first["db_ms"] >= 0
    assert set(first) <= {"request_id", "method", "route", "status", "duration_ms", "db_ms", "db_queries", "trace_id"}


def test_request_stats_windows_percentiles_and_probes():
    now = [1000.0]
    stats = RequestStats(clock=lambda: now[0])
    stats.started -= 3600
    for duration in range(1, 101):
        stats.begin()
        stats.end(float(duration), 1.0, 500 if duration > 98 else 200)
    stats.begin()
    stats.end(9999.0, 0.0, 200, counted=False)  # a health probe
    summary = stats.summary(60)
    assert summary["requests"] == 100 and summary["p50_ms"] == 50.0 and summary["p95_ms"] == 95.0
    assert summary["error_rate"] == 0.02 and summary["peak_in_flight"] == 1 and stats.in_flight == 0
    now[0] += 1000  # beyond the 15-minute history
    assert stats.summary(900)["requests"] == 0


def test_system_health_is_admin_only_and_contains_no_traveler_data(api):
    with Session(api.app.state.engine) as session:
        traveler = session.exec(select(Traveler).where(Traveler.role == "traveler")).one()
        session.add(Application(id=APPLICATION_ID, traveler_id=traveler.id, destination="SCHENGEN",
                                questionnaire_id="schengen-short-stay", questionnaire_version=1, visa_type="short_stay"))
        session.flush()
        session.add(AgentRun(id="f" * 32, traveler_id=traveler.id, application_id=APPLICATION_ID, site="schengen-harmonised"))
        session.add(Notification(traveler_id=traveler.id, kind="status_changed", message="Your application changed."))
        session.commit()
    assert api.client.get("/admin/system-health").status_code == 401
    for role in ("traveler", "support"):
        assert api.client.get("/admin/system-health", headers=api.headers(role)).status_code == 403
    api.client.get("/applications", headers=api.headers("traveler"))
    reply = api.client.get("/admin/system-health", headers=api.headers("admin"))
    assert reply.status_code == 200 and reply.headers["cache-control"] == "no-store"
    body = reply.json()
    assert body["status"] in {"ok", "warn", "fail"} and body["environment"] == "development"
    assert body["queue"]["queued"] == 1 and body["queue"]["oldest_queued_seconds"] is not None
    assert body["notifications"]["email_pending"] == 1
    assert body["database"]["reachable"] is True and body["storage"]["writable"] is True
    assert body["api"]["last_5m"]["requests"] >= 1
    assert {check["component"] for check in body["checks"]} >= {"api", "database", "memory", "data disk", "agent queue"}
    assert not any(value in reply.text for value in ("example.com", "Synthetic", APPLICATION_ID, "f" * 32))


def test_release_images_bake_sentry_release_and_public_browser_settings(monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "deploy" / "aws"))
    try:
        import publish_images
    finally:
        sys.path.pop(0)
    sha = "a" * 40
    assert publish_images.build_args("api", sha, environ={}) == ["--build-arg", f"SENTRY_RELEASE=taasheera@{sha}"]
    web = publish_images.build_args("web", sha, environ={"SENTRY_FRONTEND_DSN": "https://abc123@o1.ingest.sentry.io/42",
                                                         "SENTRY_FRONTEND_TRACES_SAMPLE_RATE": "0.2"})
    assert f"VITE_SENTRY_RELEASE=taasheera@{sha}" in web and "VITE_SENTRY_ENVIRONMENT=production" in web
    assert "VITE_SENTRY_DSN=https://abc123@o1.ingest.sentry.io/42" in web and "VITE_SENTRY_TRACES_SAMPLE_RATE=0.2" in web
    with pytest.raises(ValueError):
        publish_images.build_args("web", sha, environ={"SENTRY_FRONTEND_DSN": "javascript:alert(1)"})
    with pytest.raises(ValueError):
        publish_images.build_args("web", sha, environ={"SENTRY_FRONTEND_TRACES_SAMPLE_RATE": "5"})
