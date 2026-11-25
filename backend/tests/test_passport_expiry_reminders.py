"""Remind travelers before their passport expires (CMPS271-36)."""

from datetime import date, datetime, timedelta, timezone
from io import BytesIO

import pytest
from PIL import Image
from sqlmodel import Session, select

from app.features import FeatureFlags
from app.models import Notification, PassportExpiry, Traveler
from app.notification_jobs import (EXPIRY_REMINDERS, record_passport_expiry, remind_expiring_passports,
                                   run_notification_jobs)
from test_applications_api import api  # noqa: F401  (pytest fixture)
from test_notification_email import SITE, Outbox

TODAY = datetime.now(timezone.utc)
FIELDS = {"surname": "ERIKSSON", "given_names": "ANNA MARIA", "document_number": "L898902C3",
          "nationality": "UTO", "issuing_country": "UTO", "birth_date": "1974-08-12", "sex": "F",
          "personal_number": "ZE184226B"}


@pytest.fixture(autouse=True)
def plain_uploads(monkeypatch):
    # Uploading a passport is not what is tested here.
    monkeypatch.setenv("FEATURE_PRIVACY_CONSENT", "false")
    monkeypatch.setenv("FEATURE_PASSPORT_SCAN_QUALITY", "false")


def expires_in(days: int) -> date:
    return TODAY.date() + timedelta(days=days)


def confirm_passport(client, headers, expiry: date | None):
    image = BytesIO()
    Image.new("RGB", (800, 500), "white").save(image, format="JPEG")
    upload_id = client.post("/passports", headers=headers,
                            files={"file": ("passport.jpg", image.getvalue(), "image/jpeg")}).json()["id"]
    response = client.put(f"/passports/{upload_id}/review", headers=headers,
                          json={**FIELDS, "expiry_date": expiry.isoformat() if expiry else None})
    assert response.status_code == 200, response.json()


def saved(app, traveler_id=1) -> PassportExpiry | None:
    with Session(app.state.engine) as session:
        return session.get(PassportExpiry, traveler_id)


def reminders(app) -> list[str]:
    with Session(app.state.engine) as session:
        return [row.message for row in session.exec(select(Notification).where(
            Notification.kind == "passport_expiring").order_by(Notification.id)).all()]


def test_confirming_a_passport_saves_only_its_expiry_date(api):  # noqa: F811
    client, app, headers, _ = api
    confirm_passport(client, headers[0], date(2031, 4, 15))
    row = saved(app)
    assert (row.expiry_date, row.reminded_days) == (date(2031, 4, 15), None)
    assert saved(app, 2) is None


def test_a_passport_far_from_expiry_gets_no_reminder(api):  # noqa: F811
    client, app, headers, _ = api
    confirm_passport(client, headers[0], expires_in(181))
    assert remind_expiring_passports(app.state.engine) == 0


def test_six_months_one_month_and_expiry_each_remind_once(api):  # noqa: F811
    client, app, headers, _ = api
    confirm_passport(client, headers[0], expires_in(200))
    engine = app.state.engine
    for days_later, expected in ((19, 0), (20, 1), (25, 0), (169, 0), (170, 1), (199, 0), (200, 0), (201, 1)):
        assert remind_expiring_passports(engine, now=TODAY + timedelta(days=days_later)) == expected, days_later
    assert reminders(app) == [EXPIRY_REMINDERS[180], EXPIRY_REMINDERS[30], EXPIRY_REMINDERS[0]]
    assert remind_expiring_passports(engine, now=TODAY + timedelta(days=400)) == 0


def test_a_passport_already_close_to_expiry_gets_only_the_closest_reminder(api):  # noqa: F811
    client, app, headers, _ = api
    confirm_passport(client, headers[0], expires_in(10))
    assert remind_expiring_passports(app.state.engine) == 1
    assert reminders(app) == [EXPIRY_REMINDERS[30]]


def test_a_new_passport_starts_its_reminders_again_but_reconfirming_does_not(api):  # noqa: F811
    client, app, headers, _ = api
    confirm_passport(client, headers[0], expires_in(100))
    assert remind_expiring_passports(app.state.engine) == 1
    confirm_passport(client, headers[0], expires_in(100))  # same passport confirmed again
    assert remind_expiring_passports(app.state.engine) == 0
    confirm_passport(client, headers[0], expires_in(90))  # a different date: another passport
    assert saved(app).reminded_days is None
    assert remind_expiring_passports(app.state.engine) == 1


def test_a_passport_without_an_expiry_date_forgets_the_old_one(api):  # noqa: F811
    client, app, headers, _ = api
    confirm_passport(client, headers[0], expires_in(100))
    confirm_passport(client, headers[0], None)
    assert saved(app) is None


def test_email_has_no_passport_details_and_the_bell_links_to_the_passport_page(api):  # noqa: F811
    client, app, headers, _ = api
    confirm_passport(client, headers[0], expires_in(150))
    outbox = Outbox()
    assert run_notification_jobs(app.state.engine, outbox, SITE).sent == 1
    [(_, subject, body)] = outbox.sent
    assert subject == "New notification from Taasheera"
    assert f"{SITE}/dashboard" in body
    assert EXPIRY_REMINDERS[180] not in body and f"{SITE}/passport" not in body
    assert expires_in(150).isoformat() not in body and "L898902C3" not in body
    [item] = client.get("/notifications", headers=headers[0]).json()
    assert (item["kind"], item["link"]) == ("passport_expiring", "/passport")


def test_with_passport_emails_off_the_bell_still_reminds(api):  # noqa: F811
    client, app, headers, _ = api
    client.put("/profile/notification-preferences", headers=headers[0],
               json={"application_updates": True, "reminders": True, "passport_expiry": False})
    confirm_passport(client, headers[0], expires_in(20))
    outbox = Outbox()
    result = run_notification_jobs(app.state.engine, outbox, SITE)
    assert (result.sent, result.skipped, outbox.sent) == (0, 1, [])
    assert reminders(app) == [EXPIRY_REMINDERS[30]]


def test_suspended_accounts_are_not_reminded(api):  # noqa: F811
    _, app, _, _ = api
    record_passport_expiry(app.state.engine, 2, expires_in(5))
    with Session(app.state.engine) as session:
        traveler = session.get(Traveler, 2)
        traveler.status = "suspended"
        session.add(traveler)
        session.commit()
    assert remind_expiring_passports(app.state.engine) == 0


def test_with_the_flag_off_no_expiry_date_is_kept(api, monkeypatch):  # noqa: F811
    client, app, headers, _ = api
    flags = app.state.feature_flags
    monkeypatch.setattr(app.state, "feature_flags", FeatureFlags(flags.enabled - {"email_notifications"}))
    confirm_passport(client, headers[0], expires_in(20))
    assert saved(app) is None
