"""Every upload is scanned for malware before it is stored (CMPS271-31).

The EICAR test string is the industry's harmless antivirus test file. It is decoded at runtime and only
ever held in memory here, so no file on disk carries it.
"""

import hashlib
import logging
import socket
import struct
import threading
import zlib
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
from sqlmodel import Session, select

from app.auth import access_token
from app.main import create_app
from app.models import ActivityEvent, AuthSession, SupportingDocument, Traveler
from app.upload_scan import (DOCUMENT_TYPES, EICAR_SIGNATURE, PASSPORT_TYPES, REJECTED_MESSAGE, BuiltinScanner,
                             ClamdScanner, Infected, ScannerUnavailable, UploadScanner, clamd_address)
from test_applications_api import api, start  # noqa: F401 (api is a fixture)
from test_supporting_documents import make_pdf


def image(format: str = "JPEG", size=(400, 300)) -> bytes:
    out = BytesIO()
    Image.new("RGB", size, "white").save(out, format=format)
    return out.getvalue()


def passport_photo(format: str = "JPEG") -> bytes:
    out = BytesIO()
    specimen = Image.new("RGB", (800, 500), "white")
    ImageDraw.Draw(specimen).rectangle((100, 120, 700, 380), fill="black")
    specimen.save(out, format=format)
    return out.getvalue()


def build_pdf(objects: list[bytes]) -> bytes:
    out = BytesIO()
    out.write(b"%PDF-1.7\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    out.write("".join(f"{offset:010d} 00000 n \n" for offset in offsets).encode())
    out.write(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


def pdf(catalog: bytes = b"", *extra: bytes) -> bytes:
    return build_pdf([b"<< /Type /Catalog /Pages 2 0 R " + catalog + b" >>",
                      b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                      b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>", *extra])


def stream(dictionary: bytes, data: bytes) -> bytes:
    return b"<< " + dictionary + b" /Length " + str(len(data)).encode() + b" >>\nstream\n" + data + b"\nendstream"


JAVASCRIPT_PDF = pdf(b"/OpenAction 4 0 R", b"<< /S /JavaScript /JS (app.alert\\(1\\)) >>")

ACTIVE_PDFS = {
    "open action with JavaScript": JAVASCRIPT_PDF,
    "JS key alone": pdf(b"/OpenAction << /JS (app.alert\\(1\\)) >>"),
    "launch action": pdf(b"/OpenAction << /S /Launch /F (calc.exe) >>"),
    "embedded file": pdf(b"/Names << /EmbeddedFiles << /Names [(a.txt) 4 0 R] >> >>",
                         b"<< /Type /Filespec /F (a.txt) /EF << /F 5 0 R >> >>",
                         stream(b"/Type /EmbeddedFile", b"synthetic attachment")),
    "embedded file without the name tree": pdf(b"", stream(b"/Type /EmbeddedFile", b"synthetic attachment")),
    "additional actions": pdf(b"/AA << /WC << /S /GoTo /D [3 0 R /Fit] >> >>"),
    "hex-escaped names": pdf(b"/OpenAction << /S /J#61vaScript /J#53 (x) >>"),
    "JavaScript in a compressed object stream": pdf(b"", stream(
        b"/Type /ObjStm /N 1 /First 4 /Filter /FlateDecode",
        zlib.compress(b"9 0 << /S /JavaScript /JS (app.alert\\(1\\)) >>"))),
    "object stream it can't read": pdf(b"", stream(
        b"/Type /ObjStm /N 1 /First 4 /Filter /ASCIIHexDecode", b"3c3c202f53202f4a53203e3e>")),
}

CLEAN_PDFS = {
    "text": make_pdf("Statement for ANNA MARIA ERIKSSON"),
    "open action that only shows page one": pdf(b"/OpenAction [3 0 R /Fit]"),
    "compressed object stream with a font": pdf(b"", stream(
        b"/Type /ObjStm /N 1 /First 4 /Filter /FlateDecode",
        zlib.compress(b"9 0 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"))),
    "binary stream that happens to hold name-like bytes": pdf(b"", stream(
        b"/Filter /DCTDecode", b"\x00\x01/JS \x02/AA \x03/EF \x04\xff")),
}


def scan(data: bytes, declared: str | None = None, allowed=DOCUMENT_TYPES) -> None:
    BuiltinScanner().scan(data, declared, allowed)


def reason(data: bytes, declared: str | None = None, allowed=DOCUMENT_TYPES) -> str:
    with pytest.raises(Infected) as found:
        scan(data, declared, allowed)
    return found.value.reason


def test_the_signature_is_the_standard_eicar_test_string():
    assert hashlib.sha256(EICAR_SIGNATURE).hexdigest() == (
        "275a021bbfb6489e54d471899f7db9d1663fc695ec2fe2a2c4538aabf651fd0f")


@pytest.mark.parametrize("data", [EICAR_SIGNATURE, make_pdf() + EICAR_SIGNATURE, image() + EICAR_SIGNATURE],
                         ids=["alone", "after a PDF", "after a JPEG"])
def test_the_eicar_test_file_is_rejected_on_its_own_or_inside_another_file(data):
    assert reason(data, "application/octet-stream") == "eicar"


@pytest.mark.parametrize("header", [b"MZ\x90\x00\x03\x00\x00\x00PE\x00\x00", b"\x7fELF\x02\x01\x01",
                                    b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe",
                                    b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe"],
                         ids=["windows", "linux", "macho-32", "macho-64", "macho-32-le", "macho-64-le", "macho-fat"])
def test_executables_are_rejected_whatever_they_claim_to_be(header):
    for declared in (None, "application/octet-stream", "image/jpeg", "application/pdf"):
        assert reason(header + b"\x00" * 64, declared) == "executable"


@pytest.mark.parametrize("name", ACTIVE_PDFS)
def test_pdfs_with_active_content_are_rejected(name):
    assert reason(ACTIVE_PDFS[name], "application/pdf") == "pdf_active_content"


@pytest.mark.parametrize("name", CLEAN_PDFS)
def test_ordinary_pdfs_pass(name):
    scan(CLEAN_PDFS[name], "application/pdf")


@pytest.mark.parametrize("content, declared, allowed", [
    (image("PNG"), "image/jpeg", DOCUMENT_TYPES),
    (make_pdf(), "image/jpeg", PASSPORT_TYPES),
    (image("JPEG"), "application/pdf", DOCUMENT_TYPES),
    (b"<html><script>alert(1)</script></html>", "image/png", DOCUMENT_TYPES),
    (b"#!/bin/sh\necho not an image", "image/jpeg", PASSPORT_TYPES),
])
def test_content_that_is_not_the_declared_type_is_rejected(content, declared, allowed):
    assert reason(content, declared, allowed) == "type_mismatch"


@pytest.mark.parametrize("content, declared", [
    (image("JPEG"), "image/jpeg"), (image("JPEG"), "image/jpg"), (image("JPEG"), "IMAGE/JPEG; charset=binary"),
    (image("PNG"), "image/png"), (image("WEBP"), "image/webp"), (make_pdf(), "application/pdf"),
    (image("PNG"), "application/octet-stream"), (image("PNG"), None), (image("PNG"), ""),
    (image("GIF"), "image/gif"), (b"plain text", "text/plain"),
])
def test_matching_undeclared_and_unsupported_types_pass_the_scan_for_the_type_check_to_explain(content, declared):
    scan(content, declared)


def recv_exact(connection: socket.socket, size: int) -> bytes:
    data = b""
    while len(data) < size:
        chunk = connection.recv(size - len(data))
        if not chunk:
            raise ConnectionError("closed")
        data += chunk
    return data


@contextmanager
def fake_clamd(reply: bytes | None):
    """A one-connection clamd that reads a whole INSTREAM, then sends `reply` (or hangs up when None)."""
    server = socket.create_server(("127.0.0.1", 0))
    received = []

    def serve():
        try:
            connection, _ = server.accept()
        except OSError:
            return
        with connection:
            command = b""
            while not command.endswith(b"\0"):
                command += recv_exact(connection, 1)
            data = b""
            while size := struct.unpack(">I", recv_exact(connection, 4))[0]:
                data += recv_exact(connection, size)
            received.append((command, data))
            if reply is not None:
                connection.sendall(reply)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield server.getsockname()[1], received
    finally:
        server.close()
        thread.join(5)


def closed_port() -> int:
    with socket.create_server(("127.0.0.1", 0)) as server:
        return server.getsockname()[1]


def test_clamd_receives_the_whole_file_and_a_clean_answer_passes():
    data = image(size=(800, 600)) + bytes(200_000)
    with fake_clamd(b"stream: OK\0") as (port, received):
        ClamdScanner("127.0.0.1", port).scan(data, "image/jpeg", PASSPORT_TYPES)
    assert received == [(b"zINSTREAM\0", data)]


def test_clamd_finding_rejects_the_file():
    with fake_clamd(b"stream: Win.Test.EICAR_HDB-1 FOUND\0") as (port, _):
        with pytest.raises(Infected) as found:
            ClamdScanner("127.0.0.1", port).scan(image(), "image/jpeg", PASSPORT_TYPES)
    assert found.value.reason == "clamav"


@pytest.mark.parametrize("reply", [b"INSTREAM size limit exceeded. ERROR\0", b"UNKNOWN COMMAND\0", None])
def test_clamd_errors_and_hang_ups_fail_closed(reply):
    with fake_clamd(reply) as (port, _):
        with pytest.raises(ScannerUnavailable):
            ClamdScanner("127.0.0.1", port, timeout=5).scan(image(), "image/jpeg", PASSPORT_TYPES)


def test_an_unreachable_clamd_fails_closed():
    with pytest.raises(ScannerUnavailable):
        ClamdScanner("127.0.0.1", closed_port(), timeout=5).scan(image(), "image/jpeg", PASSPORT_TYPES)


def test_the_built_in_scanner_runs_first_and_clamd_only_when_configured(monkeypatch):
    assert [type(scanner) for scanner in UploadScanner.from_environment().scanners] == [BuiltinScanner]
    monkeypatch.setenv("CLAMD_ADDRESS", "clamav:3310")
    builtin, clamd = UploadScanner.from_environment().scanners
    assert isinstance(builtin, BuiltinScanner) and (clamd.host, clamd.port) == ("clamav", 3310)
    assert clamd_address("[::1]:3310") == ("::1", 3310)


@pytest.mark.parametrize("value", ["clamav", "clamav:", ":3310", "clamav:0", "clamav:65536", "clamav:33a0",
                                   "clam av:3310"])
def test_an_invalid_clamd_address_stops_startup(tmp_path, monkeypatch, value):
    monkeypatch.setenv("CLAMD_ADDRESS", value)
    with pytest.raises(RuntimeError, match="CLAMD_ADDRESS must be host:port"):
        create_app(f"sqlite:///{(tmp_path / 'app.db').as_posix()}")


@pytest.fixture
def documents_api(monkeypatch, api):  # noqa: F811
    monkeypatch.setattr("app.documents.files.pytesseract.image_to_string", lambda image, timeout: "OCR TEXT")
    return api


@pytest.fixture(autouse=True)
def no_privacy_prompt(monkeypatch):
    monkeypatch.setenv("FEATURE_PRIVACY_CONSENT", "false")


def events(app):
    with Session(app.state.engine) as session:
        return [(row.traveler_id, row.event_type, row.identifier, row.outcome)
                for row in session.exec(select(ActivityEvent).order_by(ActivityEvent.id))]


def stored_files(root: Path) -> list[Path]:
    return [path for path in root.rglob("*") if path.is_file()] if root.exists() else []


def upload_passport(client, headers, content, name="passport.jpg", content_type="image/jpeg"):
    return client.post("/passports", headers=headers, files={"file": (name, content, content_type)})


def upload_document(client, headers, application_id, content, requirement="bank_statements",
                    name="statement.pdf", content_type="application/pdf"):
    return client.post(f"/applications/{application_id}/documents", headers=headers,
                       data={"requirement": requirement}, files={"file": (name, content, content_type)})


@pytest.mark.parametrize("content, content_type, reason_code", [
    (EICAR_SIGNATURE, "image/jpeg", "eicar"),
    (b"MZ\x90\x00" + bytes(100), "application/octet-stream", "executable"),
    (make_pdf(), "image/jpeg", "type_mismatch"),
], ids=["eicar", "executable", "type_mismatch"])
def test_a_rejected_passport_upload_is_never_stored_and_is_recorded(api, tmp_path, caplog,  # noqa: F811
                                                                    content, content_type, reason_code):
    client, app, headers, _ = api
    with caplog.at_level(logging.INFO):
        response = upload_passport(client, headers[0], content, name="Secret Name.jpg", content_type=content_type)
    assert response.status_code == 422 and response.json() == {"detail": REJECTED_MESSAGE}
    assert stored_files(tmp_path / "passports") == []
    assert events(app)[-2:] == [(1, "upload_rejected", "passport", reason_code),
                                (1, "passport_upload", "passport", "failure")]
    [line] = [record.getMessage() for record in caplog.records if record.name == "app.upload_scan"]
    assert f"reason={reason_code}" in line and "source=passport" in line
    mirror = "".join(path.read_text() for path in (tmp_path / "activity-logs").rglob("*.jsonl"))
    for private in ("Secret Name", content[:8].decode("latin-1")):
        assert private not in caplog.text and private not in mirror


def test_a_rejected_document_is_never_stored_and_replacing_with_one_keeps_the_old_file(documents_api, tmp_path):
    client, app, headers, _ = documents_api
    application = start(client, headers[0])
    response = upload_document(client, headers[0], application["id"], JAVASCRIPT_PDF, name="bank.pdf")
    assert response.status_code == 422 and response.json()["detail"] == REJECTED_MESSAGE
    assert stored_files(tmp_path / "passports") == []
    with Session(app.state.engine) as session:
        assert session.exec(select(SupportingDocument)).all() == []
    assert events(app)[-1] == (1, "upload_rejected", "bank_statements", "pdf_active_content")

    clean = make_pdf("Statement")
    document = upload_document(client, headers[0], application["id"], clean).json()
    base = f"/applications/{application['id']}/documents/{document['id']}"
    replaced = client.put(base, headers=headers[0],
                          files={"file": ("new.pdf", make_pdf() + EICAR_SIGNATURE, "application/pdf")})
    assert replaced.status_code == 422 and replaced.json()["detail"] == REJECTED_MESSAGE
    assert events(app)[-1] == (1, "upload_rejected", "bank_statements", "eicar")
    assert client.get(f"{base}/file", headers=headers[0]).content == clean
    assert len(stored_files(tmp_path / "passports")) == 2


def test_clean_passports_and_documents_still_upload(documents_api):
    client, _, headers, _ = documents_api
    for content, content_type in ((passport_photo("JPEG"), "image/jpeg"), (passport_photo("PNG"), "image/png"),
                                  (passport_photo("WEBP"), "application/octet-stream")):
        assert upload_passport(client, headers[0], content, content_type=content_type).status_code == 201
    application = start(client, headers[0])
    for name in CLEAN_PDFS:
        response = upload_document(client, headers[0], application["id"], CLEAN_PDFS[name])
        assert response.status_code == 201, (name, response.json())
    photo = upload_document(client, headers[0], application["id"], image("PNG"), requirement="photo",
                            name="me.png", content_type="image/png")
    assert photo.status_code == 201 and photo.json()["content_type"] == "image/jpeg"


def test_switching_the_flag_off_skips_the_scan(monkeypatch, documents_api, tmp_path):
    client, app, headers, _ = documents_api
    monkeypatch.setattr(app.state, "feature_flags", type(app.state.feature_flags)(
        app.state.feature_flags.enabled - {"malware_scan"}))
    application = start(client, headers[0])
    assert upload_document(client, headers[0], application["id"], JAVASCRIPT_PDF).status_code == 201


@pytest.mark.parametrize("reply, status", [(b"stream: OK\0", 201), (b"stream: Synthetic.Test FOUND\0", 422),
                                           (b"Can't allocate memory ERROR\0", 503), (None, 503)])
def test_uploads_wait_for_clamd_and_fail_closed_without_it(tmp_path, monkeypatch, reply, status):
    with fake_clamd(reply) as (port, received):
        monkeypatch.setenv("CLAMD_ADDRESS", f"127.0.0.1:{port}")
        with api_client(tmp_path) as (client, app, headers):
            response = upload_passport(client, headers, passport_photo())
    assert response.status_code == status, response.json()
    assert len(received) == 1
    files = stored_files(tmp_path / "passports")
    assert len(files) == (1 if status == 201 else 0)
    if status == 503:
        assert "scanner unavailable" in response.json()["detail"].lower()
        assert response.headers["retry-after"] == "60"
    if status == 422:
        assert events(app)[-2][1:] == ("upload_rejected", "passport", "clamav")


def test_an_unreachable_clamd_refuses_uploads(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAMD_ADDRESS", f"127.0.0.1:{closed_port()}")
    with api_client(tmp_path) as (client, _, headers):
        response = upload_passport(client, headers, passport_photo())
    assert response.status_code == 503 and stored_files(tmp_path / "passports") == []


@contextmanager
def api_client(tmp_path):
    app = create_app(f"sqlite:///{(tmp_path / 'scan.db').as_posix()}")
    with TestClient(app) as client:
        with Session(app.state.engine) as session:
            traveler = Traveler(name="Scan Traveler", email="scan@example.com", password_hash="test-hash")
            session.add(traveler)
            session.flush()
            auth_session = AuthSession(id="scan-session", traveler_id=traveler.id, refresh_hash="s" * 64,
                                       expires_at=2**31)
            session.add(auth_session)
            session.commit()
            token = access_token(auth_session, app.state.auth_settings).access_token
        yield client, app, {"Authorization": f"Bearer {token}"}
