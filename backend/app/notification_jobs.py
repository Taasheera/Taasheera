"""Work the API does on a timer for notifications (Sprint 5).

`run_notification_jobs` runs every NOTIFICATION_JOB_SECONDS (default 15 minutes) in one background
thread of the API process, while the `email_notifications` flag is on. Each run:

- emails every notification still marked "pending" (CMPS271-98), unless the traveler turned that kind
  of email off (CMPS271-99). A notification is saved wherever its event happens, including the agent
  worker's container, so its email copy waits here.

Each email is committed as sent right after sending, so a crash loses nothing and at worst sends one
email twice. Only one API process runs (see deploy/aws/compose.yaml), so runs never overlap.
"""

import logging
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from .mailer import NotificationSender
from .models import Notification, Traveler
from .notification_preferences import wants_email

logger = logging.getLogger("taasheera.notifications")

EMAIL_ATTEMPTS = 3
# An email about something older than this is no longer useful, for example after the flag was off.
EMAIL_MAX_AGE = timedelta(days=1)
BATCH = 100

SUBJECTS = {
    "status_changed": "Update on your visa application",
    "agent_finished": "Your embassy form is filled in",
    "agent_stopped": "The agent stopped filling your embassy form",
}
DEFAULT_SUBJECT = "News from Taasheera"


@dataclass
class EmailResult:
    sent: int = 0
    skipped: int = 0
    failed: int = 0
    retrying: int = 0


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def email_body(notification: Notification, site_url: str) -> str:
    """The plain-text email: the notification's fixed sentence and a link, never personal data."""
    lines = [notification.message, ""]
    if notification.link:
        lines += [f"Open it in Taasheera: {site_url}{notification.link}", ""]
    lines += [
        "You get this email because you have a Taasheera account.",
        f"Choose which emails you get on your account page: {site_url}/account",
        "Taasheera never asks for your password or passport details by email.",
    ]
    return "\n".join(lines) + "\n"


def send_pending_emails(engine, sender: NotificationSender | None, site_url: str, *,
                        now: datetime | None = None) -> EmailResult:
    """Email each pending notification once. Without a sender, nothing is sent and nothing changes."""
    result = EmailResult()
    if sender is None:
        return result
    now = now or datetime.now(timezone.utc)
    with Session(engine) as session:
        pending = session.exec(select(Notification).where(Notification.email_status == "pending")
                               .order_by(Notification.id).limit(BATCH)).all()
        for notification in pending:
            traveler = session.get(Traveler, notification.traveler_id)
            if (traveler is None or traveler.status != "active" or _utc(notification.created_at) < now - EMAIL_MAX_AGE
                    or not wants_email(session, traveler.id, notification.kind)):
                notification.email_status = "skipped"
                result.skipped += 1
            else:
                try:
                    sender.send_notification(traveler.email, SUBJECTS.get(notification.kind, DEFAULT_SUBJECT),
                                             email_body(notification, site_url))
                except Exception as error:  # A mail server problem must not stop the other emails.
                    notification.email_attempts += 1
                    if notification.email_attempts >= EMAIL_ATTEMPTS:
                        notification.email_status = "failed"
                        result.failed += 1
                    else:
                        result.retrying += 1
                    logger.warning("Notification email %s was not sent (%s)", notification.id, type(error).__name__)
                else:
                    notification.email_status = "sent"
                    result.sent += 1
            session.add(notification)
            session.commit()
    return result


def run_notification_jobs(engine, sender: NotificationSender | None, site_url: str, *,
                          now: datetime | None = None) -> EmailResult:
    """One run of every notification job."""
    return send_pending_emails(engine, sender, site_url, now=now)


def show_logs() -> None:
    """Print this module's messages (emails logged in development, send failures) in the API's output."""
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s:     %(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def job_interval() -> int:
    """Seconds between runs; 0 turns the background thread off (tests do)."""
    try:
        seconds = int(os.environ.get("NOTIFICATION_JOB_SECONDS", "900"))
    except ValueError:
        raise RuntimeError("NOTIFICATION_JOB_SECONDS must be a whole number of seconds.") from None
    if seconds != 0 and not 60 <= seconds <= 86400:
        raise RuntimeError("NOTIFICATION_JOB_SECONDS must be 0 (off) or between 60 and 86400.")
    return seconds


class NotificationJobThread:
    """Runs the jobs every `interval` seconds until stopped. One per API process."""

    def __init__(self, run, interval: int):
        self._run = run
        self._interval = interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="notification-jobs", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=30)

    def _loop(self) -> None:
        # A short wait first, so the API is serving before the first run.
        if self._stop.wait(min(30, self._interval)):
            return
        while True:
            try:
                self._run()
            except Exception:
                logger.exception("Notification jobs failed; trying again next time.")
            if self._stop.wait(self._interval):
                return
