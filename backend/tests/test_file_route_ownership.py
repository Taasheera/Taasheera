"""Every route that returns or accepts file content checks its owner (CMPS271-114).

Another traveler gets 404, as if the file did not exist, and a signed-out request gets 401. The list
below must name every such route, so a new file route fails this test until it is covered.
"""

import base64
import io
import re
import secrets
import zipfile
from io import BytesIO

import pytest
from PIL import Image, ImageDraw
from sqlmodel import Session

from app.agent import queue
from app.agent.storage import AgentFiles
from app.models import Application
from app.passport.extraction import Extraction, ExtractionStatus
from test_applications_api import SPECIMEN, answer_all, api, start  # noqa: F401 (api is a fixture)
from test_supporting_documents import make_pdf

PNG = b"\x89PNG\r\n\x1a\n synthetic screenshot"
FILE_PATH = re.compile(r"/(image|file|packet|download|export|screenshots|extract|review|crosscheck)(/|$)")

ROUTES = {
    ("POST", "/passports"): "upload",
    ("GET", "/passports/{upload_id}/image"): "image",
    ("POST", "/passports/{upload_id}/extract"): "image",
    ("GET", "/passports/{upload_id}/review"): "passport details",
    ("PUT", "/passports/{upload_id}/review"): "passport details",
    ("POST", "/applications/{application_id}/documents"): "upload",
    ("PUT", "/applications/{application_id}/documents/{document_id}"): "upload",
    ("GET", "/applications/{application_id}/documents/{document_id}/file"): "document",
    ("GET", "/applications/{application_id}/documents/packet"): "PDF",
    ("GET", "/applications/{application_id}/documents/crosscheck"): "document text",
    ("GET", "/applications/{application_id}/agent/runs/{run_id}/screenshots/{screenshot_id}"): "screenshot",
    ("POST", "/applications/{application_id}/form/download"): "PDF",
    ("GET", "/applications/{application_id}/export"): "record",
    ("GET", "/auth/account/export"): "archive",
}


def jpeg() -> bytes:
    out = BytesIO()
    specimen = Image.new("RGB", (800, 500), "white")
    ImageDraw.Draw(specimen).rectangle((100, 120, 700, 380), fill="black")
    specimen.save(out, format="JPEG")
    return out.getvalue()


def test_the_list_names_every_route_that_returns_or_accepts_file_content(api):  # noqa: F811
    _, app, _, _ = api
    operations = [(method.upper(), path, operation) for path, methods in app.openapi()["paths"].items()
                  for method, operation in methods.items()]
    routes = {(method, path) for method, path, _ in operations}
    found = {(method, path) for method, path, operation in operations
             if "multipart/form-data" in operation.get("requestBody", {}).get("content", {})
             or FILE_PATH.search(path)}
    assert len(routes) > 50
    assert found == set(ROUTES), found ^ set(ROUTES)


@pytest.fixture
def world(monkeypatch, api, tmp_path):  # noqa: F811
    monkeypatch.setattr("app.passport.router.extract_passport",
                        lambda image, reader: Extraction(status=ExtractionStatus.UNREADABLE))
    client, app, headers, _ = api
    upload_id = client.post("/passports", headers=headers[0],
                            files={"file": ("passport.jpg", jpeg(), "image/jpeg")}).json()["id"]
    assert client.put(f"/passports/{upload_id}/review", headers=headers[0], json=SPECIMEN).status_code == 200
    application_id = start(client, headers[0])["id"]
    answer_all(client, headers[0], application_id)
    statement = make_pdf("Statement")
    document = client.post(f"/applications/{application_id}/documents", headers=headers[0],
                           data={"requirement": "bank_statements"},
                           files={"file": ("statement.pdf", statement, "application/pdf")})
    assert document.status_code == 201, document.json()
    with Session(app.state.engine) as session:
        run_id = queue.enqueue(session, session.get(Application, application_id), "schengen-harmonised").id
        assert queue.claim(session).id == run_id
        files = AgentFiles(tmp_path / "passports", cipher=app.state.passport_storage.cipher)
        shot_id = queue.record_screenshot(session, run_id, page=1, page_title="Personal details",
                                          save=lambda screenshot_id: files.save(1, application_id, screenshot_id, PNG))
    return {"upload_id": upload_id, "application_id": application_id, "document_id": document.json()["id"],
            "run_id": run_id, "screenshot_id": shot_id, "statement": statement}


@pytest.fixture(autouse=True, params=["plaintext", "encrypted"])
def storage_mode(request, monkeypatch):
    monkeypatch.setenv("FEATURE_PRIVACY_CONSENT", "false")
    if request.param == "encrypted":
        monkeypatch.setenv("DOCUMENT_ENCRYPTION_KEY", base64.b64encode(secrets.token_bytes(32)).decode())
    return request.param


def call(client, method: str, path: str, ids: dict, headers: dict | None):
    url = path.format(**ids)
    if (method, path) == ("POST", "/passports"):
        return client.post(url, headers=headers, files={"file": ("passport.jpg", jpeg(), "image/jpeg")})
    if path == "/applications/{application_id}/documents":
        return client.post(url, headers=headers, data={"requirement": "bank_statements"},
                           files={"file": ("statement.pdf", make_pdf("Another"), "application/pdf")})
    if method == "PUT" and "/documents/" in path:
        return client.put(url, headers=headers, files={"file": ("statement.pdf", make_pdf("New"), "application/pdf")})
    if method == "PUT":
        return client.put(url, headers=headers, json=SPECIMEN)
    if path.endswith("/form/download"):
        return client.post(url, headers=headers, json={"confirmation": "0" * 64, "reviewed": True})
    return client.request(method, url, headers=headers)


OWNER_STATUS = {("POST", "/passports"): 201, ("POST", "/applications/{application_id}/documents"): 201,
                ("POST", "/applications/{application_id}/form/download"): 409}


@pytest.mark.parametrize("method, path", sorted(ROUTES))
def test_another_traveler_gets_404_and_a_signed_out_request_gets_401(api, world, method, path):  # noqa: F811
    client, app, headers, _ = api
    signed_out = call(client, method, path, world, None)
    assert signed_out.status_code == 401, signed_out.json()

    other = call(client, method, path, world, headers[1])
    if (method, path) == ("POST", "/passports"):
        assert other.status_code == 201
        assert app.state.passport_storage.load(1, other.json()["id"]) is None
    elif path == "/auth/account/export":
        assert other.status_code == 200
        names = zipfile.ZipFile(io.BytesIO(other.content)).namelist()
        assert not [name for name in names if world["upload_id"] in name or world["document_id"] in name]
    else:
        assert other.status_code == 404, other.json()

    owner = call(client, method, path, world, headers[0])
    assert owner.status_code == OWNER_STATUS.get((method, path), 200), owner.text[:200]
    if path.endswith("/file"):
        assert owner.content == world["statement"]
    if path.endswith("/image"):
        assert owner.content.startswith(b"\xff\xd8\xff")
    if "/screenshots/" in path:
        assert owner.content == PNG
