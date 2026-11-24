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
