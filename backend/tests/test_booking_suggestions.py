"""Fill from your documents: uploaded bookings suggest the trip dates and the place to stay."""
from datetime import timedelta

from sqlmodel import Session, select

from app.documents.booking_dates import booked_value
from app.documents.crosscheck import DocumentText
from app.models import ApplicationAnswer, DocumentAccess
from test_applications_api import ANSWERS, ARRIVAL, answer_all, api, put, start  # noqa: F401 (api is a fixture)
from test_supporting_documents import consent_not_required, make_pdf, no_ocr, upload  # noqa: F401 (fixtures)

DEPARTURE = ARRIVAL + timedelta(days=10)


def booking(requirement: str, text: str, document_id: str = "a" * 32) -> DocumentText:
    return DocumentText(id=document_id, requirement=requirement, label=requirement.replace("_", " ").capitalize(),
                        filename="booking.pdf", text=text)


def test_flight_dates_come_before_hotel_dates():
    flight = booking("flight_booking", "Outbound flight BEY-CDG: 12 March 2027\nReturn flight: 2027-03-20")
    hotel = booking("accommodation_proof", "Check-in: 2027-03-13\nCheck-out: 2027-03-19", "b" * 32)
    assert booked_value("arrival", [hotel, flight]) == ("2027-03-12", flight)
    assert booked_value("departure", [hotel, flight]) == ("2027-03-20", flight)
    assert booked_value("arrival", [hotel]) == ("2027-03-13", hotel)


def test_disagreeing_or_unlabeled_bookings_suggest_nothing():
    first = booking("flight_booking", "Outbound flight: 2027-03-12")
    second = booking("flight_booking", "Outbound flight: 2027-04-01", "b" * 32)
    assert booked_value("arrival", [first, second]) is None
    # A hotel booking doesn't stand in when the flights disagree.
    hotel = booking("accommodation_proof", "Check-in: 2027-03-13", "c" * 32)
    assert booked_value("arrival", [first, second, hotel]) is None
    assert booked_value("arrival", [booking("flight_booking", "Booked on 2027-01-02")]) is None
    assert booked_value("arrival", [booking("flight_booking", "Outbound flight: 05/03/2027")]) is None


def test_the_stay_is_the_labeled_hotel_name_and_address():
    hotel = booking("accommodation_proof", "Booking confirmation\nGuest: Example Traveler\nEmail address: x@example.com\n"
                                           "Hotel name: Hotel Exemple\nHotel address: 2 Rue Exemple, 75001 Paris\n")
    assert booked_value("stay", [hotel]) == ("Hotel Exemple, 2 Rue Exemple, 75001 Paris", hotel)
    assert booked_value("stay", [booking("accommodation_proof", "Property: Le Petit Studio")])[0] == "Le Petit Studio"
    assert booked_value("stay", [booking("flight_booking", "Hotel: Hotel Exemple")]) is None


def hotel_answers():
    return {**ANSWERS, "accommodation": "hotel"}


def test_an_uploaded_flight_booking_suggests_the_dates(api):
    client, app, headers, _ = api
    application_id = start(client, headers[0])["id"]
    current = answer_all(client, headers[0], application_id, stop_at="arrival_date")["current"]
    assert current["suggestion"] is None
    assert current["upload_hint"] == {"requirement": "flight_booking", "label": "Flight reservation"}

    text = f"Outbound flight BEY-CDG: {ARRIVAL:%d %B %Y} Return flight: {DEPARTURE.isoformat()}"
    assert upload(client, headers[0], application_id, "flight_booking", make_pdf(text)).status_code == 201
    current = client.get(f"/applications/{application_id}", headers=headers[0]).json()["current"]
    assert current["suggestion"] == {"value": ARRIVAL.isoformat(), "source": "document",
                                     "document": "Flight reservation (statement.pdf)"}
    assert current["upload_hint"] is None
    state = client.put(f"/applications/{application_id}/answers/arrival_date", headers=headers[0],
                       json={"use_suggestion": True}).json()
    assert state["current"]["id"] == "departure_date"
    assert state["current"]["suggestion"]["value"] == DEPARTURE.isoformat()
    with Session(app.state.engine) as session:
        row = session.exec(select(ApplicationAnswer).where(ApplicationAnswer.question_id == "arrival_date")).one()
        assert (row.value, row.source) == (ARRIVAL.isoformat(), "document")
        assert session.exec(select(DocumentAccess).where(DocumentAccess.purpose == "document_prefill")).first()


def test_booking_dates_still_follow_the_rules(api):
    client, _, headers, _ = api
    application_id = start(client, headers[0])["id"]
    answer_all(client, headers[0], application_id, stop_at="arrival_date")
    upload(client, headers[0], application_id, "flight_booking", make_pdf("Outbound flight: 2001-01-01"))
    current = client.get(f"/applications/{application_id}", headers=headers[0]).json()["current"]
    # A date in the past isn't offered, and the browser can't make the server use it.
    assert current["suggestion"] is None
    response = client.put(f"/applications/{application_id}/answers/arrival_date", headers=headers[0],
                          json={"use_suggestion": True})
    assert response.status_code == 409


def test_another_travelers_booking_is_never_read(api):
    client, _, headers, _ = api
    mine = start(client, headers[0])["id"]
    theirs = start(client, headers[1])["id"]
    answer_all(client, headers[0], mine, stop_at="arrival_date")
    answer_all(client, headers[1], theirs, stop_at="arrival_date")
    upload(client, headers[1], theirs, "flight_booking", make_pdf(f"Outbound flight: {ARRIVAL.isoformat()}"))
    assert client.get(f"/applications/{mine}", headers=headers[0]).json()["current"]["suggestion"] is None


def test_hotel_booking_suggests_where_to_stay(api):
    client, _, headers, _ = api
    application_id = start(client, headers[0])["id"]
    current = answer_all(client, headers[0], application_id, answers=hotel_answers(),
                         stop_at="accommodation_details")["current"]
    assert current["upload_hint"] == {"requirement": "accommodation_proof", "label": "Accommodation booking"}
    upload(client, headers[0], application_id, "accommodation_proof", make_pdf("Hotel: Hotel Exemple"))
    current = client.get(f"/applications/{application_id}", headers=headers[0]).json()["current"]
    assert current["suggestion"]["value"] == "Hotel Exemple"


def test_nothing_is_suggested_or_hinted_while_documents_are_off(api, monkeypatch):
    client, app, headers, _ = api
    application_id = start(client, headers[0])["id"]
    answer_all(client, headers[0], application_id, stop_at="arrival_date")
    upload(client, headers[0], application_id, "flight_booking", make_pdf(f"Outbound flight: {ARRIVAL.isoformat()}"))
    monkeypatch.setattr(app.state, "feature_flags", type(app.state.feature_flags)(frozenset()))
    current = client.get(f"/applications/{application_id}", headers=headers[0]).json()["current"]
    assert current["suggestion"] is None and current["upload_hint"] is None
