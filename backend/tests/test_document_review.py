"""Uploaded documents in the cross-check page and the completeness check (CMPS271-50)."""

from sqlmodel import Session, select

from app.applications.router import application_context
from app.models import DocumentAccess
from test_applications_api import ANSWERS, PROFILE, answer_all, api, start  # noqa: F401 (api is a fixture)
from test_supporting_documents import consent_not_required, make_pdf, make_png, no_ocr, upload  # noqa: F401 (fixtures)

FILLER = " Booking reference EXAMPLE123, issued by Example Travel for the dates you gave."


def required_uploads(client, headers, application_id, booking_text):
    for requirement in ("photo", "travel_insurance", "bank_statements", "invitation_letter", "pension_statement"):
        assert upload(client, headers, application_id, requirement, make_pdf("Anna Maria Eriksson" + FILLER)).status_code == 201
    assert upload(client, headers, application_id, "flight_booking", make_pdf(booking_text + FILLER)).status_code == 201


def check(client, headers, application_id):
    response = client.get(f"/applications/{application_id}/check", headers=headers)
    assert response.status_code == 200
    return response.json()


def test_missing_documents_block_the_completeness_check(api):
    client, _, headers, _ = api
    application_id = start(client, headers[0])["id"]
    answer_all(client, headers[0], application_id)  # retired, staying with a host
    report = check(client, headers[0], application_id)
    assert not report["ready"]
    missing = [issue["message"] for issue in report["issues"] if issue["kind"] == "missing"]
    assert "Not uploaded yet: Invitation from your host" in missing
    assert "Not uploaded yet: Pension statements" in missing
    assert not any("Cover letter" in message for message in missing)  # optional


def test_a_document_that_disagrees_with_the_passport_is_a_warning(api):
    client, app, headers, _ = api
    application_id = start(client, headers[0])["id"]
    answer_all(client, headers[0], application_id)
    required_uploads(client, headers[0], application_id, "Passenger ANNA ERICSSON passport no L898902C3")
    report = check(client, headers[0], application_id)
    assert report["ready"] is True
    [warning] = [issue for issue in report["issues"] if issue["severity"] == "warning" and issue["document_id"] != "photo"
                 and not issue["link"]]
    assert "Flight reservation (statement.pdf) spells your surname ERICSSON" in warning["message"]

    crosscheck = client.get(f"/applications/{application_id}/documents/crosscheck", headers=headers[0])
    assert crosscheck.status_code == 200 and crosscheck.headers["cache-control"] == "private, no-store"
    body = crosscheck.json()
    assert body["reference"] == "application"
    assert [(f["field"], f["requirement"], f["found"]) for f in body["findings"]] == [("name", "flight_booking", "ERICSSON")]
    assert len(body["checked"]) == 5 and body["unreadable"] == []  # the photo is not compared

    # The assistant's view of the application carries the same warning.
    with Session(app.state.engine) as session:
        context = application_context(session, 1, application_id, document_issues=app.state.document_issues)
    assert "Completeness check: ready, with warnings" in context and "ERICSSON" in context


def test_the_cross_check_compares_the_profile_too_and_is_owner_only(api):
    client, app, headers, _ = api
    assert client.put("/profile", headers=headers[0], json={**PROFILE, "surname": "ERIKSEN"}).status_code == 200
    application_id = start(client, headers[0])["id"]
    answer_all(client, headers[0], application_id)
    body = client.get(f"/applications/{application_id}/documents/crosscheck", headers=headers[0]).json()
    assert [(f["source"], f["field"]) for f in body["findings"]] == [("profile", "name")]
    assert client.get(f"/applications/{application_id}/documents/crosscheck", headers=headers[1]).status_code == 404


def test_reading_documents_for_the_check_is_audited(api):
    client, app, headers, _ = api
    application_id = start(client, headers[0])["id"]
    answer_all(client, headers[0], application_id)
    document = upload(client, headers[0], application_id, "bank_statements", make_png()).json()
    photo = upload(client, headers[0], application_id, "photo", make_png()).json()
    client.get(f"/applications/{application_id}/documents/crosscheck", headers=headers[0])
    with Session(app.state.engine) as session:
        purposes = [row.purpose for row in session.exec(select(DocumentAccess)
                                                        .where(DocumentAccess.upload_id == document["id"]))]
    assert purposes == ["document_check"]
    # A photo has no text to compare, so it isn't read at all.
    with Session(app.state.engine) as session:
        assert session.exec(select(DocumentAccess).where(DocumentAccess.upload_id == photo["id"])).all() == []


def test_with_the_flag_off_the_completeness_check_is_unchanged(api, monkeypatch):
    client, app, headers, _ = api
    monkeypatch.setattr(app.state, "feature_flags", type(app.state.feature_flags)(frozenset()))
    application_id = start(client, headers[0])["id"]
    answer_all(client, headers[0], application_id)
    report = check(client, headers[0], application_id)
    assert report["ready"] and report["readiness_score"] == 100
    assert report["answered"] == report["total"] == 21 and not report["issues"]
