"""The official online form guide (UK, US, Canada and other portal-only visas)."""
from datetime import date, timedelta

import pytest

from app.applications import flow
from app.applications.portal_guide import build_guide, load_guide
from test_applications_api import SPECIMEN, api, put  # noqa: F401

GUIDED = ["uk-standard-visitor", "us-b2-tourist", "canada-visitor"]


@pytest.mark.parametrize("questionnaire_id", GUIDED)
def test_every_guide_matches_its_questionnaire(questionnaire_id):
    guide = load_guide(questionnaire_id)
    assert guide is not None and guide.portal.url.startswith("https://")
    questionnaire = flow.load_questionnaire(questionnaire_id)
    used = {source.split(".")[0] for section in guide.sections for field in section.fields for source in field.sources}
    # The passport and trip dates always reach the official form.
    assert {"passport", "arrival_date", "departure_date"} <= used
    assert all(questionnaire.question(key) for key in used - {"account_email"})


def test_schengen_uses_its_pdf_not_a_portal_guide():
    assert load_guide("schengen-short-stay") is None


def test_values_use_the_portal_date_format_and_form_wording():
    questionnaire = flow.load_questionnaire("us-b2-tourist")
    guide = load_guide("us-b2-tourist")
    answers = {"passport": SPECIMEN, "arrival_date": "2027-03-15", "occupation": "homemaker",
               "home_address": {"line1": "1 Example Street", "city": "Beirut", "country": "LBN"}}
    view = build_guide(guide, questionnaire, answers, "anna@example.com")
    fields = {field.label: field for section in view.sections for field in section.fields}
    assert fields["Intended date of arrival"].value == "15-Mar-2027"
    assert fields["Primary occupation"].value == "Homemaker"
    assert fields["Home address"].value == "1 Example Street, Beirut, LBN"
    assert fields["Country/region of origin (nationality)"].kind == "country"
    assert fields["Passport/travel document type"].value == "Regular"
    assert fields["Email address"].value == "anna@example.com"
    assert fields["Intended date of departure"].value is None
    assert 0 < view.filled < view.total


def test_owner_reads_the_guide_and_others_get_404(api):
    client, _, headers, _ = api
    owner, other = headers
    application_id = client.post("/applications", headers=owner,
                                 json={"destination": "GBR", "visa_type": "standard_visitor"}).json()["id"]
    assert put(client, owner, application_id, "purpose", "tourism").status_code == 200
    response = client.get(f"/applications/{application_id}/portal-guide", headers=owner)
    assert response.status_code == 200 and response.headers["cache-control"] == "private, no-store"
    body = response.json()
    assert body["portal"]["url"].startswith("https://www.gov.uk/")
    reason = next(field for section in body["sections"] for field in section["fields"] if field["label"] == "Main reason for your visit")
    assert reason["value"] == "Tourism or a holiday"
    assert client.get(f"/applications/{application_id}/portal-guide", headers=other).status_code == 404
    schengen = client.post("/applications", headers=owner, json={"destination": "SCHENGEN", "visa_type": "short_stay"}).json()["id"]
    assert client.get(f"/applications/{schengen}/portal-guide", headers=owner).status_code == 404
