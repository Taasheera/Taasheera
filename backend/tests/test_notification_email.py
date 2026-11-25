"""Email copies of notifications (CMPS271-98)."""

import threading
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.main import create_app
from app.models import Notification, Traveler
from app.notification_jobs import (EMAIL_ATTEMPTS, NotificationJobThread, job_interval, run_notification_jobs,
                                   send_pending_emails)
from app.notifications import notify
from test_applications_api import answer_all, api, start  # noqa: F401  (api is a fixture)

SITE = "https://taasheera.example"
TITLE = "Schengen short-stay visa (type C)"


class Outbox:
    """A mail server that keeps what it was given, and can be told to refuse."""

    def __init__(self, failures: int = 0):
        self.sent: list[tuple[str, str, str]] = []
        self.failures = failures

    def send_notification(self, recipient, subject, body):
        if self.failures:
            self.failures -= 1
            raise ConnectionError("mail server unavailable")
        self.sent.append((recipient, subject, body))


def stored(app) -> list[Notification]:
    with Session(app.state.engine) as session:
        return list(session.exec(select(Notification).order_by(Notification.id)).all())


def add(app, traveler_id=1, *, created_at=None, kind="status_changed", link="/dashboard/abc"):
    with Session(app.state.engine) as session:
        row = notify(session, traveler_id, kind, "Your visa application has news.", link=link)
        if created_at:
            row.created_at = created_at
        session.commit()
        return row.id


def test_a_status_change_is_emailed_once_with_no_private_details(api):  # noqa: F811
    client, app, headers, _ = api
    application_id = start(client, headers[0])["id"]
    answer_all(client, headers[0], application_id)
    [notification] = stored(app)
    assert notification.email_status == "pending"

    outbox = Outbox()
    result = send_pending_emails(app.state.engine, outbox, SITE)
    assert (result.sent, result.skipped, result.failed) == (1, 0, 0)
    [(recipient, subject, body)] = outbox.sent
    with Session(app.state.engine) as session:
        assert recipient == session.get(Traveler, 1).email
    assert subject == "New notification from Taasheera"
    assert body.startswith("You have a new notification.")
    assert f"{SITE}/dashboard\n" in body
    # Fixed sentences only: no passport fields, answers or the traveler's name.
    for private in ("ERIKSSON", "ANNA", "L898902C", "1974", TITLE, application_id):
        assert private not in body
    assert stored(app)[0].email_status == "sent"

    assert send_pending_emails(app.state.engine, outbox, SITE).sent == 0
    assert len(outbox.sent) == 1


def test_all_kinds_use_the_same_generic_alert(api):  # noqa: F811
    _, app, _, _ = api
    for kind in ("agent_finished", "agent_stopped", "something_new"):
        add(app, kind=kind)
    outbox = Outbox()
    send_pending_emails(app.state.engine, outbox, SITE)
    assert len(outbox.sent) == 3
    assert len({(subject, body) for _, subject, body in outbox.sent}) == 1


def test_a_notification_without_a_link_still_reads_well(api):  # noqa: F811
    _, app, _, _ = api
    add(app, link=None)
    outbox = Outbox()
    send_pending_emails(app.state.engine, outbox, SITE)
    [(_, _, body)] = outbox.sent
    assert f"{SITE}/dashboard" in body
    assert "Your visa application has news." not in body


def test_without_a_mail_server_nothing_is_sent_or_marked(api):  # noqa: F811
    _, app, _, _ = api
    add(app)
    result = send_pending_emails(app.state.engine, None, SITE)
    assert (result.sent, result.skipped) == (0, 0)
    assert stored(app)[0].email_status == "pending"


def test_a_failing_mail_server_is_retried_then_given_up_and_never_blocks_others(api):  # noqa: F811
    _, app, _, _ = api
    first, second = add(app), add(app, traveler_id=2)
    outbox = Outbox(failures=1)
    result = send_pending_emails(app.state.engine, outbox, SITE)
    assert (result.sent, result.retrying) == (1, 1)
    rows = {row.id: row for row in stored(app)}
    assert (rows[first].email_status, rows[first].email_attempts) == ("pending", 1)
    assert rows[second].email_status == "sent"

    always_down = Outbox(failures=99)
    for _ in range(EMAIL_ATTEMPTS):
        send_pending_emails(app.state.engine, always_down, SITE)
    row = {row.id: row for row in stored(app)}[first]
    assert (row.email_status, row.email_attempts) == ("failed", EMAIL_ATTEMPTS)
    assert send_pending_emails(app.state.engine, Outbox(), SITE).sent == 0


def test_old_news_and_suspended_accounts_are_skipped_not_emailed(api):  # noqa: F811
    _, app, _, _ = api
    old = add(app, created_at=datetime.now(timezone.utc) - timedelta(days=2))
    add(app, traveler_id=2)
    with Session(app.state.engine) as session:
        traveler = session.get(Traveler, 2)
        traveler.status = "suspended"
        session.add(traveler)
        session.commit()
    outbox = Outbox()
    result = send_pending_emails(app.state.engine, outbox, SITE)
    assert (result.sent, result.skipped) == (0, 2) and outbox.sent == []
    assert {row.id: row.email_status for row in stored(app)}[old] == "skipped"


def test_the_jobs_run_on_a_timer_until_stopped():
    runs = threading.Event()
    calls = []

    def run():
        calls.append(1)
        if len(calls) >= 2:
            runs.set()

    jobs = NotificationJobThread(run, interval=0.01)
    jobs.start()
    assert runs.wait(5)
    jobs.stop()
    stopped_at = len(calls)
    threading.Event().wait(0.05)
    assert len(calls) == stopped_at


def test_a_failing_run_does_not_stop_the_timer():
    calls = []
    done = threading.Event()

    def run():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("database unavailable")
        done.set()

    jobs = NotificationJobThread(run, interval=0.01)
    jobs.start()
    assert done.wait(5)
    jobs.stop()


@pytest.mark.parametrize(("value", "expected"), [("0", 0), ("60", 60), ("900", 900)])
def test_the_interval_setting(monkeypatch, value, expected):
    monkeypatch.setenv("NOTIFICATION_JOB_SECONDS", value)
    assert job_interval() == expected


@pytest.mark.parametrize("value", ["5", "-1", "a while", "90000"])
def test_a_bad_interval_setting_stops_startup(monkeypatch, value):
    monkeypatch.setenv("NOTIFICATION_JOB_SECONDS", value)
    with pytest.raises(RuntimeError, match="NOTIFICATION_JOB_SECONDS"):
        job_interval()


def started(monkeypatch, tmp_path, *, flag: str, interval: str = "60", sender=None):
    monkeypatch.setenv("FEATURE_EMAIL_NOTIFICATIONS", flag)
    monkeypatch.setenv("NOTIFICATION_JOB_SECONDS", interval)
    threads = []
    monkeypatch.setattr(NotificationJobThread, "start", lambda self: threads.append(self))
    monkeypatch.setattr(NotificationJobThread, "stop", lambda self: None)
    app = create_app(f"sqlite:///{(tmp_path / 'jobs.db').as_posix()}", notification_sender=sender)
    with TestClient(app):
        pass
    return threads


def test_the_api_starts_the_timer_only_while_the_flag_is_on(monkeypatch, tmp_path):
    assert len(started(monkeypatch, tmp_path, flag="true", sender=Outbox())) == 1
    assert started(monkeypatch, tmp_path, flag="false", sender=Outbox()) == []
    assert started(monkeypatch, tmp_path, flag="true", interval="0", sender=Outbox()) == []
    # Development without a mail server still runs it, writing the emails to the log.
    assert len(started(monkeypatch, tmp_path, flag="true")) == 1


def test_development_without_a_mail_server_logs_only_the_attempt(api, caplog):  # noqa: F811
    from app.mailer import LogNotificationSender
    from app.notification_jobs import logger

    _, app, _, _ = api
    add(app)
    with caplog.at_level("INFO", logger="taasheera.notifications"):
        assert run_notification_jobs(app.state.engine, LogNotificationSender(logger), SITE).sent == 1
    assert "not sent, no mail server" in caplog.text
    with Session(app.state.engine) as session:
        email = session.get(Traveler, 1).email
    assert email not in caplog.text
    assert email.split("@")[1] not in caplog.text
    assert SITE not in caplog.text
    assert "Your visa application has news." not in caplog.text


def test_notification_messages_are_shown_in_the_api_output():
    import logging

    from app.notification_jobs import logger, show_logs

    show_logs()
    show_logs()  # starting twice adds no second copy of each line
    assert logger.isEnabledFor(logging.INFO)
    assert len(logger.handlers) == 1
