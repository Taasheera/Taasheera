"""Staging-only support for the Sprint 5 whole-app capacity test, and the scaling settings it tries.

Each stand-in is refused outside staging, so none of it can weaken production."""
import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session, select

import manage
from app.config import AuthSettings
from app.consent import PRIVACY_POLICY_VERSION
from app.database import build_engine
from app.load_testing import FAKE_REPLY, fake_assistant_from_environment
from app.main import create_app
from app.models import PrivacyConsent, Traveler
from app.schema import migration_config

EXEMPT, OTHER = "203.0.113.7", "198.51.100.9"


def failed_logins(client, address, count):
    statuses = []
    for attempt in range(count):
        reply = client.post("/auth/login", json={"email": f"nobody{attempt % 9}@example.com", "password": "Wrong-password-123"},
                            headers={"X-Taasheera-Client-IP": f"{address}:{40000 + attempt}"})
        statuses.append(reply.status_code)
    return statuses


def test_load_generator_skips_sign_in_limits_only_on_staging(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTH_TRUST_CLOUDFRONT_PROXY", "true")
    monkeypatch.setenv("DEPLOY_ENVIRONMENT", "staging")
    monkeypatch.setenv("THROTTLE_EXEMPT_IPS", f"{EXEMPT},192.0.2.0/28")
    with TestClient(create_app(f"sqlite:///{tmp_path / 'limits.db'}")) as client:
        assert 429 not in failed_logins(client, EXEMPT, 35)
        others = failed_logins(client, OTHER, 35)
        # Everyone else keeps the 30-per-15-minutes limit.
        assert others[:30] == [401] * 30 and set(others[30:]) == {429}


@pytest.mark.parametrize("environment", ["production", "development"])
def test_rate_limit_exemption_is_refused_outside_staging(monkeypatch, environment):
    monkeypatch.setenv("DEPLOY_ENVIRONMENT", environment)
    monkeypatch.setenv("THROTTLE_EXEMPT_IPS", EXEMPT)
    with pytest.raises(RuntimeError, match="only on staging"):
        AuthSettings.from_environment()


@pytest.mark.parametrize("value", ["not-an-ip", "10.0.0.0/8"])
def test_rate_limit_exemption_must_be_a_few_addresses(monkeypatch, value):
    monkeypatch.setenv("DEPLOY_ENVIRONMENT", "staging")
    monkeypatch.setenv("THROTTLE_EXEMPT_IPS", value)
    with pytest.raises(RuntimeError, match="THROTTLE_EXEMPT_IPS"):
        AuthSettings.from_environment()


def test_seed_load_test_creates_synthetic_accounts_once_and_only_on_staging(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'seed.db'}")
    command.upgrade(migration_config(), "head")
    monkeypatch.setenv("LOADTEST_PASSWORD", "synthetic-load-password")
    monkeypatch.setenv("LOADTEST_ADMIN_PASSWORD", "synthetic-admin-password")
    monkeypatch.setenv("DEPLOY_ENVIRONMENT", "production")
    with pytest.raises(SystemExit, match="only on staging"):
        manage.seed_load_test(3, "loadtest")
    monkeypatch.setenv("DEPLOY_ENVIRONMENT", "staging")
    assert manage.seed_load_test(3, "loadtest") == "4 synthetic accounts created; 0 already existed."
    assert manage.seed_load_test(3, "loadtest") == "0 synthetic accounts created; 4 already existed."
    engine = build_engine()
    try:
        with Session(engine) as session:
            accounts = session.exec(select(Traveler).order_by(Traveler.email)).all()
            assert [account.email for account in accounts] == [
                "loadtest-0001@example.com", "loadtest-0002@example.com", "loadtest-0003@example.com", "loadtest-admin@example.com"]
            assert [account.role for account in accounts] == ["traveler", "traveler", "traveler", "admin"]
            consents = session.exec(select(PrivacyConsent)).all()
            assert len(consents) == 4 and {consent.policy_version for consent in consents} == {PRIVACY_POLICY_VERSION}
    finally:
        engine.dispose()
    monkeypatch.setenv("LOADTEST_PASSWORD", "short")
    with pytest.raises(SystemExit, match="12 characters"):
        manage.seed_load_test(3, "loadtest")


def test_assistant_stand_in_is_staging_only_and_replies_without_gemini(monkeypatch):
    monkeypatch.setenv("ASSISTANT_FAKE_LATENCY_MS", "5")
    monkeypatch.setenv("DEPLOY_ENVIRONMENT", "production")
    with pytest.raises(RuntimeError, match="only on staging"):
        fake_assistant_from_environment()
    monkeypatch.setenv("DEPLOY_ENVIRONMENT", "staging")
    ask, ask_tools = fake_assistant_from_environment()
    assert ask("system", []) == FAKE_REPLY and ask_tools("system", []).call is None
    assert "not legal or immigration advice" in FAKE_REPLY
    monkeypatch.setenv("ASSISTANT_FAKE_LATENCY_MS", "60000")
    with pytest.raises(RuntimeError):
        fake_assistant_from_environment()


def test_assistant_stand_in_answers_through_the_api(tmp_path, monkeypatch):
    import bcrypt
    monkeypatch.setenv("DEPLOY_ENVIRONMENT", "staging")
    monkeypatch.setenv("ASSISTANT_FAKE_LATENCY_MS", "1")
    app = create_app(f"sqlite:///{tmp_path / 'assistant.db'}")
    with TestClient(app) as client:
        with Session(app.state.engine) as session:
            session.add(Traveler(name="Synthetic", email="assist@example.com",
                                 password_hash=bcrypt.hashpw(b"Synthetic-password-1", bcrypt.gensalt(rounds=4)).decode()))
            session.commit()
        token = client.post("/auth/login", json={"email": "assist@example.com", "password": "Synthetic-password-1"}).json()["access_token"]
        reply = client.post("/assistant/messages", headers={"Authorization": f"Bearer {token}"},
                            json={"message": "Which documents do I need?", "step": "general", "history": []})
        assert reply.status_code == 200 and "load-test reply" in reply.text


def test_sqlite_write_ahead_log_is_opt_in(tmp_path, monkeypatch):
    engine = build_engine(f"sqlite:///{tmp_path / 'default.db'}")
    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA journal_mode")).scalar() == "delete"
    engine.dispose()
    monkeypatch.setenv("SQLITE_JOURNAL_MODE", "wal")
    engine = build_engine(f"sqlite:///{tmp_path / 'wal.db'}")
    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert connection.execute(text("PRAGMA synchronous")).scalar() == 1  # NORMAL
    engine.dispose()
    monkeypatch.setenv("SQLITE_JOURNAL_MODE", "memory")
    with pytest.raises(RuntimeError):
        build_engine(f"sqlite:///{tmp_path / 'other.db'}")


def test_every_endpoint_thread_gets_its_own_sqlite_connection(tmp_path, monkeypatch):
    # Capacity run 2: behind SQLAlchemy's default pool (5 + 10) and 40 endpoint threads, a burst of
    # sign-ins, each keeping its connection while hashing, left other requests waiting 30 s and failing.
    assert build_engine(f"sqlite:///{tmp_path / 'default.db'}").pool.size() == 40
    monkeypatch.setenv("API_THREADS", "24")
    engine = build_engine(f"sqlite:///{tmp_path / 'pool.db'}")
    assert engine.pool.size() == 24
    connections = [engine.connect() for _ in range(24 + 10)]  # every thread, plus background jobs
    for connection in connections:
        assert connection.execute(text("SELECT 1")).scalar() == 1
        connection.close()
    engine.dispose()
    # In-memory databases (some tests) keep SQLAlchemy's own pool for them.
    with build_engine("sqlite://").connect() as connection:
        assert connection.execute(text("SELECT 1")).scalar() == 1


@pytest.mark.parametrize("value", ["5", "500", "many"])
def test_api_threads_must_be_sensible(tmp_path, monkeypatch, value):
    monkeypatch.setenv("API_THREADS", value)
    with pytest.raises(RuntimeError, match="API_THREADS"):
        with TestClient(create_app(f"sqlite:///{tmp_path / 'threads.db'}")):
            pass


def signed_up(tmp_path, monkeypatch):
    import bcrypt
    app = create_app(f"sqlite:///{tmp_path / 'login.db'}")
    client = TestClient(app)
    client.__enter__()
    with Session(app.state.engine) as session:
        session.add(Traveler(name="Synthetic", email="login@example.com",
                             password_hash=bcrypt.hashpw(b"Synthetic-password-1", bcrypt.gensalt(rounds=4)).decode()))
        session.commit()
    return app, client


def test_sign_in_checks_the_password_without_holding_the_database_write_lock(tmp_path, monkeypatch):
    """Capacity bottleneck: bcrypt used to run inside the row lock, which on SQLite blocks every writer."""
    import bcrypt as real_bcrypt
    app, client = signed_up(tmp_path, monkeypatch)
    wrote = []
    checkpw = real_bcrypt.checkpw

    def check_while_someone_writes(password, hashed):
        # Another request writes while this one hashes; it must not wait for the sign-in to finish.
        with app.state.engine.connect() as other:
            other.exec_driver_sql("PRAGMA busy_timeout = 200")
            other.execute(text("UPDATE traveler SET last_login_method = 'x' WHERE email = 'nobody@example.com'"))
            other.commit()
        wrote.append(True)
        return checkpw(password, hashed)

    monkeypatch.setattr("app.auth.bcrypt.checkpw", check_while_someone_writes)
    try:
        reply = client.post("/auth/login", json={"email": "login@example.com", "password": "Synthetic-password-1"})
        assert reply.status_code == 200 and wrote == [True]
    finally:
        client.__exit__(None, None, None)


def test_a_password_reset_between_checking_and_signing_in_still_wins(tmp_path, monkeypatch):
    import bcrypt as real_bcrypt
    from app.models import AuthSession
    app, client = signed_up(tmp_path, monkeypatch)
    checkpw = real_bcrypt.checkpw

    def reset_lands_meanwhile(password, hashed):
        matched = checkpw(password, hashed)
        with Session(app.state.engine) as other:
            traveler = other.exec(select(Traveler).where(Traveler.email == "login@example.com")).one()
            traveler.password_hash = real_bcrypt.hashpw(b"Brand-new-password-2", real_bcrypt.gensalt(rounds=4)).decode()
            other.add(traveler)
            other.commit()
        return matched

    monkeypatch.setattr("app.auth.bcrypt.checkpw", reset_lands_meanwhile)
    try:
        reply = client.post("/auth/login", json={"email": "login@example.com", "password": "Synthetic-password-1"})
        assert reply.status_code == 401
        with Session(app.state.engine) as session:
            assert session.exec(select(AuthSession)).all() == []
    finally:
        client.__exit__(None, None, None)


def test_only_one_api_process_runs_the_notification_timer(tmp_path, monkeypatch):
    from filelock import FileLock
    monkeypatch.setenv("NOTIFICATION_JOB_SECONDS", "60")
    holder = FileLock(str(tmp_path / ".notification-jobs.lock"))
    holder.acquire(timeout=0)  # another worker process already runs it
    try:
        started = []
        monkeypatch.setattr("app.main.NotificationJobThread.start", lambda self: started.append(self))
        monkeypatch.setattr("app.main.NotificationJobThread.stop", lambda self: None)
        with TestClient(create_app(f"sqlite:///{tmp_path / 'jobs.db'}")):
            assert started == []
    finally:
        holder.release()
    with TestClient(create_app(f"sqlite:///{tmp_path / 'jobs.db'}")):
        assert len(started) == 1
