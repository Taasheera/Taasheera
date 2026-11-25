"""Work the API does on a timer for notifications (Sprint 5).

`run_notification_jobs` runs every NOTIFICATION_JOB_SECONDS (default 15 minutes) in one background
thread of the API process, while the `email_notifications` flag is on. Each run:

- reminds the owner of each draft application left untouched for STALL_AFTER (CMPS271-101), once
  per stall: working on it again and leaving it another week earns a new reminder;
- reminds travelers whose confirmed passport expires within six months, within a month, or has
  expired (CMPS271-36), once each per passport;
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
from datetime import date, datetime, timedelta, timezone

from sqlmodel import Session, select

from .mailer import NotificationSender
from .models import Application, Notification, PassportExpiry, Traveler
from .notification_preferences import wants_email
from .notifications import application_name, notify

logger = logging.getLogger("taasheera.notifications")

EMAIL_ATTEMPTS = 3
# An email about something older than this is no longer useful, for example after the flag was off.
EMAIL_MAX_AGE = timedelta(days=1)
BATCH = 100
STALL_AFTER = timedelta(days=7)

SUBJECTS = {
    "status_changed": "Update on your visa application",
    "agent_finished": "Your embassy form is filled in",
    "agent_stopped": "The agent stopped filling your embassy form",
    "application_stalled": "Your visa application is waiting for you",
    "passport_expiring": "A reminder about your passport",
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


def remind_stalled_applications(engine, *, now: datetime | None = None) -> int:
    """Add one reminder for each draft application untouched for a week. Returns how many were added."""
    now = now or datetime.now(timezone.utc)
    added = 0
    with Session(engine) as session:
        drafts = session.exec(select(Application).where(Application.status == "DRAFT",
                                                        Application.archived_at.is_(None))).all()
        for application in drafts:
            touched = _utc(application.updated_at)
            if touched > now - STALL_AFTER:
                continue
            owner = session.get(Traveler, application.traveler_id)
            if owner is None or owner.status != "active":
                continue
            reminders = session.exec(select(Notification.created_at).where(
                Notification.application_id == application.id, Notification.kind == "application_stalled")).all()
            if any(_utc(sent) >= touched for sent in reminders):
                continue  # Already reminded about this stall.
            reminder = notify(session, application.traveler_id, "application_stalled",
                              f"Your {application_name(application)} application has been waiting for a week. "
                              "Open it to continue where you left off.",
                              application_id=application.id, link=f"/dashboard/{application.id}")
            reminder.created_at = now
            session.commit()
            added += 1
    return added


# Days before expiry for each reminder, closest last. 0 means it has expired. No dates in the text.
EXPIRY_REMINDERS = {
    180: ("Your passport expires in less than six months. Many embassies want at least six months of "
          "validity left, so check this before you apply."),
    30: "Your passport expires in less than a month. Renew it before you apply for a visa.",
    0: "Your passport has expired. Renew it before you apply for a visa, then upload the new one.",
}


def record_passport_expiry(engine, traveler_id: int, expiry_date: date | None) -> None:
    """Remember the confirmed passport's expiry date. A new date starts its reminders again."""
    with Session(engine) as session:
        row = session.get(PassportExpiry, traveler_id)
        if expiry_date is None:
            if row is not None:
                session.delete(row)
        elif row is None:
            session.add(PassportExpiry(traveler_id=traveler_id, expiry_date=expiry_date))
        elif row.expiry_date != expiry_date:
            row.expiry_date, row.reminded_days = expiry_date, None
            row.recorded_at = datetime.now(timezone.utc)
            session.add(row)
        session.commit()


def _expiry_level(days_left: int) -> int | None:
    if days_left < 0:
        return 0
    if days_left <= 30:
        return 30
    if days_left <= 180:
        return 180
    return None


def remind_expiring_passports(engine, *, now: datetime | None = None) -> int:
    """Add the passport reminders that are due. Returns how many were added."""
    now = now or datetime.now(timezone.utc)
    today = now.date()
    added = 0
    with Session(engine) as session:
        for row in session.exec(select(PassportExpiry)).all():
            level = _expiry_level((row.expiry_date - today).days)
            if level is None or (row.reminded_days is not None and row.reminded_days <= level):
                continue
            owner = session.get(Traveler, row.traveler_id)
            if owner is None or owner.status != "active":
                continue
            reminder = notify(session, row.traveler_id, "passport_expiring", EXPIRY_REMINDERS[level], link="/passport")
            reminder.created_at = now
            row.reminded_days = level
            session.add(row)
            session.commit()
            added += 1
    return added


def run_notification_jobs(engine, sender: NotificationSender | None, site_url: str, *,
                          now: datetime | None = None) -> EmailResult:
    """One run of every notification job: first add the reminders that are due, then email."""
    remind_stalled_applications(engine, now=now)
    remind_expiring_passports(engine, now=now)
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
