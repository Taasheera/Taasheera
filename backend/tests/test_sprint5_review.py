from datetime import date
from io import BytesIO

from pypdf import PdfReader

from app.applications.policy_sources import SOURCES, link_citations
from app.documents.booking_dates import booking_findings
from app.documents.crosscheck import DocumentText
from test_applications_api import PROFILE, answer_all, api, put, start  # noqa: F401


def test_download_checks_review_warning_consent_and_current_answers(api, monkeypatch):
    client, app, headers, _ = api
    monkeypatch.setattr(app.state, "feature_flags", type(app.state.feature_flags)(frozenset({"embassy_pdf"})))
    owner, other = headers
    application_id = start(client, owner)["id"]
    url = f"/applications/{application_id}/form"
    assert client.get(url, headers=other).status_code == 404
    assert client.get(url, headers=owner).json()["confirmation"] is None
    answer_all(client, owner, application_id)
    assert client.put("/profile", headers=owner, json={**PROFILE, "surname": "SPECIMEN"}).status_code == 200

    review = client.get(url, headers=owner).json()
    assert review["report"]["ready"] and review["report"]["readiness_score"] == 100
    assert review["report"]["issues"]
    token = review["confirmation"]
    user_confirmation = {"confirmation": token, "reviewed": True, "acknowledged_warnings": True}
    assert client.post(f"{url}/download", headers=other, json=user_confirmation).status_code == 404
    assert client.post(f"{url}/download", headers=owner, json={"confirmation": token, "reviewed": False}).status_code == 422
    assert client.post(f"{url}/download", headers=owner, json={"confirmation": token, "reviewed": True}).status_code == 409

    result = client.post(f"{url}/download", headers=owner, json=user_confirmation)
    assert result.status_code == 200
    assert result.headers["content-type"] == "application/pdf"
    assert result.headers["cache-control"] == "private, no-store"
    pdf = PdfReader(BytesIO(result.content))
    assert len(pdf.pages) == 4
    fields = pdf.get_fields()
    assert fields["surname"]["/V"] == "ERIKSSON" and fields["purpose"]["/V"] == "X"
    assert fields["leave"]["/V"]
    assert "signature" not in fields
    widgets = [entry.get_object() for page in pdf.pages for entry in page.get("/Annots", [])
               if entry.get_object().get("/Subtype") == "/Widget"]
    assert len(widgets) == len(fields)
    assert all(widget["/V"] == fields[widget["/T"]]["/V"] and widget["/AP"]["/N"].get_data()
               for widget in widgets)

    assert put(client, owner, application_id, "place_of_birth", "Example City").status_code == 200
    assert client.post(f"{url}/download", headers=owner, json=user_confirmation).status_code == 409


def test_booking_dates_use_only_unambiguous_labeled_dates():
    arrival, departure = date(2027, 1, 5), date(2027, 1, 12)
    insurance = DocumentText("sample", "travel_insurance", "Insurance", "specimen.pdf",
                             "Coverage start: 2027-01-07\nCoverage end: 2027-01-14")
    [finding] = booking_findings(insurance, arrival, departure)
    assert finding.field == "travel_dates"
    assert finding.requirement == "travel_insurance" and "uncovered" in finding.message
    itinerary = DocumentText("sample", "flight_booking", "Flights", "specimen.pdf",
                             "Outbound arrival: 05/01/2027. Invoice date: 2027-02-20.")
    assert not booking_findings(itinerary, arrival, departure)


def test_only_server_defined_policy_links_survive():
    response = link_citations("See [source:uk-standard-visitor], [source:invented] or https://example.org/unsafe.")
    assert SOURCES["uk-standard-visitor"][1] in response
    assert "invented" not in response and "example.org" not in response


def test_booking_dates_read_written_months_and_flag_nights_outside_the_trip():
    arrival, departure = date(2027, 3, 15), date(2027, 3, 25)
    hotel = DocumentText("sample", "accommodation_proof", "Accommodation booking", "hotel.jpg",
                         "Guest name: ANNA MARIA ERIKSSON\nCheck-in: 15 March 2027\nCheck-out: 28 March 2027")
    [finding] = booking_findings(hotel, arrival, departure)
    assert "outside your travel dates" in finding.message and finding.found == "2027-03-15 to 2027-03-28"
    inside = DocumentText("sample", "accommodation_proof", "Accommodation booking", "hotel.jpg",
                          "Check-in date: Mar 16, 2027\nCheck-out date: 20-Mar-2027")
    assert not booking_findings(inside, arrival, departure)
    flights = DocumentText("sample", "flight_booking", "Flight reservation", "flight.jpg",
                           "Outbound: BEY-CDG 14 March 2027\nReturn: CDG-BEY 25 March 2027")
    assert not booking_findings(flights, arrival, departure), "an overnight outbound flight is a day early"
    late = DocumentText("sample", "flight_booking", "Flight reservation", "flight.jpg",
                        "Outbound: BEY-CDG 15 March 2027\nReturn: CDG-BEY 30 March 2027")
    assert "flight dates" in booking_findings(late, arrival, departure)[0].message
    conflicting = DocumentText("sample", "accommodation_proof", "Accommodation booking", "hotel.jpg",
                               "Check-out: 28 March 2027 ... Check-out: 2 April 2027 Check-in: 15 March 2027")
    assert not booking_findings(conflicting, arrival, departure), "two different check-out dates are not guessed"


def test_official_pdf_uses_form_wording_and_job_title():
    from app.applications import flow
    from app.applications.form_pdf import build_values, load_form
    from app.agent.sites import site_for
    from app.agent.values import schengen_values
    questionnaire = flow.load_questionnaire("schengen-short-stay")
    form, _ = load_form("schengen-short-stay")
    passport = {"surname": "ERIKSSON", "given_names": "ANNA MARIA", "document_number": "L898902C3"}
    answers = {"passport": passport, "occupation": "employed", "job_title": "Accountant"}
    values = {field.key: (review.value, marker) for field, review, marker in build_values(form, questionnaire, answers, "a@example.com")}
    assert values["occupation"][0] == "Accountant"
    assert values["document_type"] == ("Ordinary passport", "ordinary")
    without_title = {field.key: review.value for field, review, _ in
                     build_values(form, questionnaire, {"passport": passport, "occupation": "retired"}, "a@example.com")}
    assert without_title["occupation"] == "Retired"
    site = site_for("schengen-short-stay")
    assert schengen_values(answers, email="a@example.com", site=site)["occupation"] == "Accountant"
    assert schengen_values({"occupation": "employed"}, email="a@example.com", site=site)["occupation"] == "Employee"
