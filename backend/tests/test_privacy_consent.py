"""Accept the privacy policy before uploading identity documents (CMPS271-115, US 30)."""

from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
from sqlmodel import Session, select

from app import consent
from app.main import create_app
from app.models import PrivacyConsent
from test_applications_api import api, start  # noqa: F401 (api is a fixture)
from test_supporting_documents import make_pdf

CURRENT = consent.PRIVACY_POLICY_VERSION


@pytest.fixture(autouse=True)
def plain_uploads(monkeypatch):
    # Upload checks unrelated to consent: no OCR, no scan-quality rejection of the synthetic image.
    monkeypatch.setenv("FEATURE_PASSPORT_SCAN_QUALITY", "false")
    monkeypatch.setattr("app.documents.files.pytesseract.image_to_string", lambda image, timeout: "OCR TEXT")


def specimen() -> bytes:
    image = BytesIO()
    picture = Image.new("RGB", (800, 500), "white")
    ImageDraw.Draw(picture).rectangle((100, 120, 700, 380), fill="black")
    picture.save(image, format="JPEG")
    return image.getvalue()


def upload_passport(client, headers):
    return client.post("/passports", files={"file": ("specimen.jpg", specimen(), "image/jpeg")}, headers=headers)


def upload_document(client, headers, application_id):
    return client.post(f"/applications/{application_id}/documents", headers=headers,
                       data={"requirement": "bank_statements"},
                       files={"file": ("statement.pdf", make_pdf("Statement"), "application/pdf")})


def accept(client, headers, version=CURRENT):
    return client.post("/profile/privacy-consent", headers=headers, json={"policy_version": version})


def rows(app, traveler_id=None):
    with Session(app.state.engine) as session:
        query = select(PrivacyConsent).order_by(PrivacyConsent.id)
        if traveler_id is not None:
            query = query.where(PrivacyConsent.traveler_id == traveler_id)
        return list(session.exec(query).all())


def test_a_new_traveler_has_not_accepted_the_current_version(api):  # noqa: F811
    client, _, headers, _ = api
    response = client.get("/profile/privacy-consent", headers=headers[0])
    assert response.status_code == 200
    assert response.json() == {"policy_version": CURRENT, "accepted": False, "accepted_at": None}
    assert response.headers["cache-control"] == "private, no-store"


def test_no_passport_or_document_is_accepted_before_the_policy(api):  # noqa: F811
    client, app, headers, storage = api
    application_id = start(client, headers[0])["id"]

    for response in (upload_passport(client, headers[0]), upload_document(client, headers[0], application_id)):
        assert response.status_code == 403
        assert response.json()["detail"] == consent.CONSENT_REQUIRED
        assert response.headers["x-privacy-consent"] == "required"
    # Nothing was stored for the refused uploads.
    assert not (storage.root / "1").exists() or not any((storage.root / "1").iterdir())
    assert client.get(f"/applications/{application_id}/documents",
                      headers=headers[0]).json()["requirements"][0]["documents"] == []
    assert rows(app) == []


def test_accepting_saves_the_version_and_time_then_uploads_work(api):  # noqa: F811
    client, app, headers, _ = api
    response = accept(client, headers[0])
    assert response.status_code == 200
    body = response.json()
    assert body["accepted"] is True and body["policy_version"] == CURRENT and body["accepted_at"]
    assert client.get("/profile/privacy-consent", headers=headers[0]).json() == body

    [row] = rows(app)
    assert (row.traveler_id, row.policy_version) == (1, CURRENT) and row.accepted_at is not None

    assert upload_passport(client, headers[0]).status_code == 201
    application_id = start(client, headers[0])["id"]
    assert upload_document(client, headers[0], application_id).status_code == 201


def test_accepting_again_keeps_one_record_and_the_first_time(api):  # noqa: F811
    client, app, headers, _ = api
    first = accept(client, headers[0]).json()
    second = accept(client, headers[0]).json()
    assert second == first
    assert len(rows(app)) == 1


def test_an_outdated_or_unknown_version_is_not_accepted(api):  # noqa: F811
    client, app, headers, _ = api
    for version in ("2026-10-31", "anything"):
        response = accept(client, headers[0], version)
        assert response.status_code == 409
        assert "Reload" in response.json()["detail"]
    assert accept(client, headers[0], "").status_code == 422
    assert client.post("/profile/privacy-consent", headers=headers[0],
                       json={"policy_version": CURRENT, "accepted": True}).status_code == 422
    assert rows(app) == []
    assert upload_passport(client, headers[0]).status_code == 403


def test_one_travelers_acceptance_does_not_count_for_another(api):  # noqa: F811
    client, app, headers, _ = api
    accept(client, headers[0])
    assert client.get("/profile/privacy-consent", headers=headers[1]).json()["accepted"] is False
    assert upload_passport(client, headers[1]).status_code == 403
    assert [row.traveler_id for row in rows(app)] == [1]


def test_a_new_policy_version_asks_again_and_keeps_the_old_record(api, monkeypatch):  # noqa: F811
    client, app, headers, _ = api
    accept(client, headers[0])
    application_id = start(client, headers[0])["id"]
    document = upload_document(client, headers[0], application_id).json()

    monkeypatch.setattr(consent, "PRIVACY_POLICY_VERSION", "2027-01-15")
    status = client.get("/profile/privacy-consent", headers=headers[0]).json()
    assert status == {"policy_version": "2027-01-15", "accepted": False, "accepted_at": None}
    assert upload_passport(client, headers[0]).status_code == 403
    # Replacing a file is uploading a new one.
    replaced = client.put(f"/applications/{application_id}/documents/{document['id']}", headers=headers[0],
                          files={"file": ("statement.pdf", make_pdf("New statement"), "application/pdf")})
    assert replaced.status_code == 403

    assert accept(client, headers[0], "2027-01-15").json()["accepted"] is True
    assert [row.policy_version for row in rows(app, 1)] == [CURRENT, "2027-01-15"]
    assert upload_passport(client, headers[0]).status_code == 201


def test_signed_out_requests_and_other_sites_are_refused(api):  # noqa: F811
    client, app, headers, _ = api
    assert client.get("/profile/privacy-consent").status_code == 401
    assert client.post("/profile/privacy-consent", json={"policy_version": CURRENT}).status_code == 401
    other_site = client.post("/profile/privacy-consent", json={"policy_version": CURRENT},
                             headers={**headers[0], "Origin": "https://attacker.example"})
    assert other_site.status_code == 403
    assert rows(app) == []


def test_with_the_flag_off_the_routes_are_hidden_and_uploads_work_as_before(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATURE_PRIVACY_CONSENT", "false")
    monkeypatch.setenv("PASSPORT_STORAGE_DIR", str(tmp_path / "passports"))
    app = create_app(f"sqlite:///{(tmp_path / 'off.db').as_posix()}")
    with TestClient(app) as client:
        client.post("/auth/register", json={"name": "Maya", "email": "maya@example.com",
                                            "password": "a long synthetic password"})
        token = client.post("/auth/login", json={"email": "maya@example.com",
                                                 "password": "a long synthetic password"}).json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        assert client.get("/profile/privacy-consent", headers=headers).status_code == 404
        assert accept(client, headers).status_code == 404
        assert upload_passport(client, headers).status_code == 201
        assert client.get("/features").json()["privacy_consent"] is False


def test_the_version_is_the_date_on_the_published_policy():
    # Changing the policy text means changing both, so everyone is asked to accept the new text.
    html = (Path(__file__).resolve().parents[2] / "frontend/public/privacy.html").read_text(encoding="utf-8")
    assert f'<time datetime="{CURRENT}">' in html
