"""The price breakdown, the simulated payment that unlocks submission, and its receipt (CMPS271-106, 108, 109)."""

import io
import re
from datetime import datetime, timedelta, timezone

import pytest
from pypdf import PdfReader
from sqlmodel import Session, select

from app.applications.visa_guides import load_guides
from app.models import ActivityEvent, Application, Notification, Payment
from app.payments.gate import PAYMENT_REQUIRED
from app.payments.health import payment_health
from app.payments.provider import PaymentFailed, SimulatedProvider
from app.payments.quote import SERVICE_FEE, build_quote, parse_fee
from app.payments.router import NOT_AVAILABLE
from test_applications_api import answer_all, api, start  # noqa: F401 (api is a fixture)

KEY = "0f8b7c2e-5d4a-4c3b-9a1f-2e6d8c7b5a40"


def ready_application(client, headers):
    application_id = start(client, headers)["id"]
    answer_all(client, headers, application_id)
    return application_id


def payments(client, headers, application_id, path="", method="get", **kwargs):
    return getattr(client, method)(f"/applications/{application_id}/payments{path}", headers=headers, **kwargs)


def pay(client, headers, application_id, key=KEY):
    return payments(client, headers, application_id, method="post", json={"idempotency_key": key})


def rows(app, model, *conditions):
    with Session(app.state.engine) as session:
        return list(session.exec(select(model).where(*conditions)).all())


@pytest.mark.parametrize("text, expected", [
    ("€90", ("EUR", 9000)), ("£135", ("GBP", 13500)), ("$185", ("USD", 18500)), ("US$44.40", ("USD", 4440)),
    ("CAD 100", ("CAD", 10000)), ("€1,250.50", ("EUR", 125050)),
    ("About ¥15,000", None), ("Varies by passport", None), ("Free", None), ("€90 or more", None),
])
def test_only_fixed_fees_are_read_as_amounts(text, expected):
    assert parse_fee(text) == expected


@pytest.mark.parametrize("questionnaire_id, currency, lines, at_embassy", [
    ("schengen-short-stay", "EUR", [("service_fee", 2700), ("embassy_fee", 9000)], None),
    ("uk-standard-visitor", "GBP", [("service_fee", 2300), ("embassy_fee", 13500)], None),
    ("us-b2-tourist", "USD", [("service_fee", 2900), ("embassy_fee", 18500)], None),
    ("canada-visitor", "CAD", [("service_fee", 3900), ("embassy_fee", 10000)], None),
    ("china-tourist-l", "USD", [("service_fee", 2900), ("embassy_fee", 2300)], None),
    ("india-tourist", "USD", [("service_fee", 2900), ("embassy_fee", 10300)], None),
    ("japan-short-stay", "USD", [("service_fee", 2900)], "Single entry: About ¥15,000"),
    ("turkiye-evisa", "USD", [("service_fee", 2900)], "e-Visa: Varies by passport"),
])
def test_each_destination_is_quoted_in_one_currency_from_its_guide(questionnaire_id, currency, lines, at_embassy):
    quote = build_quote(Application(id="a" * 32, traveler_id=1, questionnaire_id=questionnaire_id,
                                    questionnaire_version=1, destination="X", visa_type="x"))
    guide = next(guide for guide in load_guides() if guide.id == questionnaire_id)
    assert quote.currency == currency and currency in SERVICE_FEE
    assert [(line.kind, line.amount_minor) for line in quote.lines] == lines
    assert quote.total_minor == sum(amount for _, amount in lines)
    assert quote.paid_at_embassy == at_embassy
    assert quote.fee_source == guide.fees.source and quote.checked_on == guide.checked_on
    if at_embassy is None:
        assert quote.lines[1].label == f"Embassy fee: {guide.fees.items[0].label}"


def test_the_breakdown_is_shown_before_paying_and_payment_waits_for_a_complete_application(api):
    client, _, headers, _ = api
    draft = start(client, headers[0])["id"]
    response = payments(client, headers[0], draft)
    assert response.status_code == 200 and response.headers["cache-control"] == "private, no-store"
    state = response.json()
    assert state["quote"]["currency"] == "EUR" and state["quote"]["total_minor"] == 11700
    assert [line["kind"] for line in state["quote"]["lines"]] == ["service_fee", "embassy_fee"]
    assert state["simulated"] is True and state["available"] is True and state["payments"] == []
    assert state["can_pay"] is False and "Answer every question" in state["reason"]
    assert pay(client, headers[0], draft).status_code == 409


def test_submission_answers_402_until_the_application_is_paid_for(api):
    client, app, headers, _ = api
    application_id = ready_application(client, headers[0])
    agent = client.get(f"/applications/{application_id}/agent", headers=headers[0]).json()
    assert agent["can_start"] is False and agent["payment_required"] is True and agent["reason"] == PAYMENT_REQUIRED
    refused = client.post(f"/applications/{application_id}/agent/runs", headers=headers[0])
    assert refused.status_code == 402 and refused.json() == {"detail": PAYMENT_REQUIRED}
    assert client.get(f"/applications/{application_id}/documents/packet", headers=headers[0]).status_code == 200

    assert payments(client, headers[0], application_id).json()["can_pay"] is True
    response = pay(client, headers[0], application_id)
    assert response.status_code == 201
    payment = response.json()
    assert payment["status"] == "paid" and payment["provider"] == "simulated"
    assert payment["amount_minor"] == 11700 and payment["currency"] == "EUR" and payment["paid_at"]
    assert payment["receipt_number"] == f"TA-{payment['id'][:10].upper()}"

    agent = client.get(f"/applications/{application_id}/agent", headers=headers[0]).json()
    assert agent["can_start"] is True and agent["payment_required"] is False and agent["reason"] is None
    assert client.post(f"/applications/{application_id}/agent/runs", headers=headers[0]).status_code == 201
    [stored] = rows(app, Payment, Payment.application_id == application_id)
    assert stored.provider_reference.startswith("sim_") and stored.line_items[1]["amount_minor"] == 9000


def test_paying_twice_records_one_payment_and_one_notification(api):
    client, app, headers, _ = api
    application_id = ready_application(client, headers[0])
    first = pay(client, headers[0], application_id)
    again = pay(client, headers[0], application_id)
    other_key = pay(client, headers[0], application_id, key="a" * 32)
    assert first.status_code == 201 and again.status_code == 200 and other_key.status_code == 200
    assert first.json()["id"] == again.json()["id"] == other_key.json()["id"]
    assert len(rows(app, Payment)) == 1
    [notice] = rows(app, Notification, Notification.kind == "payment_completed")
    assert notice.application_id == application_id and notice.link == f"/dashboard/{application_id}"
    assert "went through" in notice.message and "117" not in notice.message and "EUR" not in notice.message
    state = payments(client, headers[0], application_id).json()
    assert state["can_pay"] is False and [p["id"] for p in state["payments"]] == [first.json()["id"]]


def test_the_server_sets_every_amount_and_accepts_no_card_details(api):
    client, app, headers, _ = api
    application_id = ready_application(client, headers[0])
    for body in ({"idempotency_key": KEY, "amount_minor": 1}, {"idempotency_key": KEY, "currency": "USD"},
                 {"idempotency_key": KEY, "card_number": "4242424242424242"}, {"idempotency_key": "short"}, {}):
        assert payments(client, headers[0], application_id, method="post", json=body).status_code == 422
    assert rows(app, Payment) == []
    assert pay(client, headers[0], application_id).json()["amount_minor"] == 11700


def test_another_traveler_can_not_see_pay_or_download(api):
    client, _, headers, _ = api
    application_id = ready_application(client, headers[0])
    payment_id = pay(client, headers[0], application_id).json()["id"]
    assert payments(client, headers[1], application_id).status_code == 404
    assert pay(client, headers[1], application_id, key="b" * 32).status_code == 404
    assert payments(client, headers[1], application_id, f"/{payment_id}/receipt").status_code == 404
    theirs = start(client, headers[1])["id"]
    assert payments(client, headers[1], theirs, f"/{payment_id}/receipt").status_code == 404
    assert client.get(f"/applications/{application_id}/payments").status_code == 401


def test_the_receipt_is_an_audited_pdf_with_the_lines_paid_and_no_card_data(api):
    client, app, headers, _ = api
    application_id = ready_application(client, headers[0])
    payment = pay(client, headers[0], application_id).json()
    response = payments(client, headers[0], application_id, f"/{payment['id']}/receipt")
    assert response.status_code == 200 and response.headers["content-type"] == "application/pdf"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["content-disposition"] == f'attachment; filename="taasheera-receipt-{payment["receipt_number"]}.pdf"'
    reader = PdfReader(io.BytesIO(response.content))
    assert len(reader.pages) == 1
    text = reader.pages[0].extract_text()
    for expected in (payment["receipt_number"], "Simulated payment — no card was charged", "Schengen short-stay visa",
                     "Traveler 1", "Ta'asheera service fee", "EUR 27.00",
                     "Embassy fee: Adults and children aged 12 and over", "EUR 90.00", "Total paid", "EUR 117.00"):
        assert expected in text, expected
    paid_on = datetime.fromisoformat(payment["paid_at"]).strftime("%d %B %Y")
    assert paid_on in text
    [stored] = rows(app, Payment)
    assert stored.provider_reference in text
    assert not re.search(r"\d{12,19}", text.replace(stored.provider_reference, "").replace(" ", ""))
    assert "card number" not in text.lower() and "expiry" not in text.lower() and "cvc" not in text.lower()
    assert not {"card", "pan", "cvc", "cvv", "expiry"} & {column.split("_")[0] for column in Payment.model_fields}
    assert "L898902C3" not in text and "ERIKSSON" not in text
    [event] = rows(app, ActivityEvent, ActivityEvent.event_type == "receipt_download")
    assert event.traveler_id == 1 and event.identifier == "payment"


def test_only_a_paid_payment_has_a_receipt(api, monkeypatch):
    client, app, headers, _ = api
    application_id = ready_application(client, headers[0])

    def refuse(self, payment):
        raise PaymentFailed

    charge = SimulatedProvider.charge
    monkeypatch.setattr(SimulatedProvider, "charge", refuse)
    failed = pay(client, headers[0], application_id)
    assert failed.status_code == 402 and "nothing was charged" in failed.json()["detail"]
    [row] = rows(app, Payment)
    assert row.status == "failed" and row.paid_at is None
    assert payments(client, headers[0], application_id, f"/{row.id}/receipt").status_code == 404
    assert client.post(f"/applications/{application_id}/agent/runs", headers=headers[0]).status_code == 402
    assert rows(app, Notification, Notification.kind == "payment_completed") == []

    monkeypatch.setattr(SimulatedProvider, "charge", charge)
    assert pay(client, headers[0], application_id).json()["status"] == "failed"
    retried = pay(client, headers[0], application_id, key="c" * 32)
    assert retried.status_code == 201 and retried.json()["status"] == "paid"


def test_archived_recorded_and_unsupported_applications_can_not_be_paid_for(api):
    client, app, headers, _ = api
    application_id = ready_application(client, headers[0])
    client.post(f"/applications/{application_id}/archive", headers=headers[0])
    assert pay(client, headers[0], application_id).status_code == 409
    client.post(f"/applications/{application_id}/unarchive", headers=headers[0])
    client.post(f"/applications/{application_id}/decision", headers=headers[0], json={"record": "SUBMITTED"})
    assert "recorded as submitted" in pay(client, headers[0], application_id).json()["detail"]

    uk = client.post("/applications", headers=headers[0], json={"destination": "GBR", "visa_type": "standard_visitor"}).json()["id"]
    state = payments(client, headers[0], uk).json()
    assert state["available"] is False and state["reason"] == NOT_AVAILABLE and state["quote"]["currency"] == "GBP"
    assert pay(client, headers[0], uk).status_code == 409
    assert rows(app, Payment) == []


def test_payment_opens_only_while_the_agent_can_fill_the_form(api, monkeypatch):
    client, app, headers, _ = api
    application_id = ready_application(client, headers[0])
    flags = type(app.state.feature_flags)
    monkeypatch.setattr(app.state, "feature_flags", flags(app.state.feature_flags.enabled - {"agent_form_filling"}))
    state = payments(client, headers[0], application_id).json()
    assert state["available"] is False and state["can_pay"] is False
    assert pay(client, headers[0], application_id).status_code == 409


def test_with_the_flag_off_the_routes_are_hidden_and_submission_is_not_gated(api, monkeypatch):
    client, app, headers, _ = api
    application_id = ready_application(client, headers[0])
    flags = type(app.state.feature_flags)
    monkeypatch.setattr(app.state, "feature_flags", flags(app.state.feature_flags.enabled - {"payments"}))
    assert payments(client, headers[0], application_id).status_code == 404
    assert pay(client, headers[0], application_id).status_code == 404
    agent = client.get(f"/applications/{application_id}/agent", headers=headers[0]).json()
    assert agent["can_start"] is True and agent["payment_required"] is False
    assert client.post(f"/applications/{application_id}/agent/runs", headers=headers[0]).status_code == 201


def test_deleting_the_application_deletes_its_payments(api):
    client, app, headers, _ = api
    application_id = ready_application(client, headers[0])
    pay(client, headers[0], application_id)
    assert client.delete(f"/applications/{application_id}", headers=headers[0]).status_code == 204
    assert rows(app, Payment) == []


def test_payment_health_counts_the_last_day_by_status_and_ages_the_oldest_pending(api):
    _, app, _, _ = api
    now = datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc)
    with Session(app.state.engine) as session:
        assert payment_health(session, now=now) == {
            "last_24_hours": {"pending": 0, "paid": 0, "failed": 0}, "oldest_pending_seconds": None}
        session.add(Application(id="a" * 32, traveler_id=1, questionnaire_id="schengen-short-stay",
                                questionnaire_version=1, destination="SCHENGEN", visa_type="short_stay"))
        for number, (status, hours) in enumerate([("paid", 1), ("paid", 2), ("failed", 3), ("pending", 5),
                                                  ("pending", 30), ("paid", 48)]):
            session.add(Payment(id=f"{number}".ljust(32, "p"), traveler_id=1, application_id="a" * 32,
                                amount_minor=100, currency="EUR", line_items=[], status=status,
                                idempotency_key=f"{number}".ljust(16, "k"), created_at=now - timedelta(hours=hours)))
        session.commit()
        assert payment_health(session, now=now) == {
            "last_24_hours": {"pending": 1, "paid": 2, "failed": 1}, "oldest_pending_seconds": 30 * 3600}
