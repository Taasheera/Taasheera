"""Supporting document upload, list, replace and delete (CMPS271-29)."""

from io import BytesIO
import stat

import pytest
from PIL import Image
from pypdf import PdfWriter
from sqlmodel import Session, select

from app.documents.files import read_text, check_document
from app.documents.router import clean_filename
from app.models import DocumentAccess, SupportingDocument
from test_applications_api import ANSWERS, answer_all, api, start  # noqa: F401 (api is a fixture)


def make_pdf(text: str = "") -> bytes:
    """A one-page PDF whose text layer says `text`. Synthetic content only."""
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    out.write("".join(f"{offset:010d} 00000 n \n" for offset in offsets).encode())
    out.write(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


def make_image(format: str = "PNG") -> bytes:
    out = BytesIO()
    Image.new("RGB", (40, 30), "white").save(out, format=format)
    return out.getvalue()


def make_png() -> bytes:
    return make_image("PNG")


@pytest.fixture(autouse=True)
def consent_not_required(monkeypatch):
    # Uploading before accepting the privacy policy is tested in test_privacy_consent.py.
    monkeypatch.setenv("FEATURE_PRIVACY_CONSENT", "false")


@pytest.fixture(autouse=True)
def no_ocr(monkeypatch):
    # Image text comes from Tesseract, which isn't needed to test the API.
    monkeypatch.setattr("app.documents.files.pytesseract.image_to_string", lambda image, timeout: "OCR TEXT")


def upload(client, headers, application_id, requirement, content, name="statement.pdf"):
    return client.post(f"/applications/{application_id}/documents", headers=headers,
                       data={"requirement": requirement}, files={"file": (name, content, "application/octet-stream")})


def checklist(client, headers, application_id):
    response = client.get(f"/applications/{application_id}/documents", headers=headers)
    assert response.status_code == 200, response.json()
    return response.json()


def test_checklist_follows_the_answers(api):
    client, _, headers, _ = api
    application = start(client, headers[0])
    before = checklist(client, headers[0], application["id"])
    assert [r["id"] for r in before["requirements"]] == [
        "photo", "travel_insurance", "flight_booking", "bank_statements", "cover_letter"]
    assert before["complete"] is False and before["read_only"] is False and before["other"] == []

    answer_all(client, headers[0], application["id"])  # retired, staying with a host
    after = [r["id"] for r in checklist(client, headers[0], application["id"])["requirements"]]
    assert {"invitation_letter", "pension_statement"} <= set(after)
    assert "employment_letter" not in after and "accommodation_proof" not in after


def test_upload_list_and_download_are_owner_only(api, tmp_path):
    client, app, headers, _ = api
    application = start(client, headers[0])
    response = upload(client, headers[0], application["id"], "bank_statements", make_pdf("Statement"),
                      name="../../Bank   statement.PDF")
    assert response.status_code == 201, response.json()
    document = response.json()
    assert document["filename"] == "Bank statement.pdf"
    assert document["content_type"] == "application/pdf" and len(document["id"]) == 32
    photo = upload(client, headers[0], application["id"], "photo", make_png(), name="me.png").json()
    assert photo["content_type"] == "image/jpeg" and photo["filename"] == "me.jpg"

    listed = checklist(client, headers[0], application["id"])
    by_id = {r["id"]: r["documents"] for r in listed["requirements"]}
    assert [d["id"] for d in by_id["bank_statements"]] == [document["id"]]
    assert [d["id"] for d in by_id["photo"]] == [photo["id"]]

    url = f"/applications/{application['id']}/documents/{document['id']}/file"
    downloaded = client.get(url, headers=headers[0])
    assert downloaded.status_code == 200 and downloaded.content.startswith(b"%PDF-")
    assert downloaded.headers["content-disposition"].startswith("attachment;")
    assert downloaded.headers["x-content-type-options"] == "nosniff"
    assert downloaded.headers["cache-control"] == "private, no-store"
    assert "sandbox" in downloaded.headers["content-security-policy"]

    # Another traveler can't list, read, replace or delete it, and can't tell it exists.
    for response in (client.get(f"/applications/{application['id']}/documents", headers=headers[1]),
                     client.get(url, headers=headers[1]),
                     upload(client, headers[1], application["id"], "photo", make_png()),
                     client.delete(f"/applications/{application['id']}/documents/{document['id']}", headers=headers[1])):
        assert response.status_code == 404

    # Stored privately in the owner's own folder, under our id, with the text read from it.
    folder = tmp_path / "passports" / "1" / "documents"
    assert (folder / f"{document['id']}.pdf").read_bytes().startswith(b"%PDF-")
    assert (folder / f"{document['id']}.txt").read_text().strip() == "Statement"
    assert stat.S_IMODE((folder / f"{document['id']}.pdf").stat().st_mode) == 0o600
    with Session(app.state.engine) as session:
        accesses = session.exec(select(DocumentAccess).where(DocumentAccess.upload_id == document["id"])).all()
        assert [access.purpose for access in accesses] == ["document_view"]


def test_documents_of_another_application_are_not_found(api):
    client, _, headers, _ = api
    first, second = start(client, headers[0]), start(client, headers[0])
    document = upload(client, headers[0], first["id"], "photo", make_png()).json()
    assert client.get(f"/applications/{second['id']}/documents/{document['id']}/file", headers=headers[0]).status_code == 404


@pytest.mark.parametrize("requirement, content, status, message", [
    ("employment_letter", make_pdf(), 422, "doesn't ask for that document"),
    ("nonsense", make_pdf(), 422, "doesn't ask for that document"),
    ("photo", make_image("GIF"), 415, "PDF, JPEG, PNG or WebP"),
    ("photo", b"plain text, not a document", 422, "PDF, JPEG, PNG or WebP"),
    ("photo", b"%PDF-1.4 broken", 422, "readable PDF"),
    ("photo", b"x" * (10 * 1024 * 1024 + 1), 413, "10 MB"),
])
def test_bad_uploads_are_rejected(api, requirement, content, status, message):
    client, _, headers, _ = api
    application = start(client, headers[0])
    response = upload(client, headers[0], application["id"], requirement, content)
    assert response.status_code == status and message in response.json()["detail"]
    assert checklist(client, headers[0], application["id"])["requirements"][0]["documents"] == []


def test_password_protected_pdfs_are_rejected():
    writer = PdfWriter()
    writer.add_blank_page(100, 100)
    writer.encrypt("secret")
    out = BytesIO()
    writer.write(out)
    with pytest.raises(ValueError, match="password-protected"):
        check_document(out.getvalue())


def test_each_document_holds_a_limited_number_of_files(api):
    client, _, headers, _ = api
    application = start(client, headers[0])
    for _ in range(5):
        assert upload(client, headers[0], application["id"], "bank_statements", make_pdf()).status_code == 201
    response = upload(client, headers[0], application["id"], "bank_statements", make_pdf())
    assert response.status_code == 409 and "up to 5 files" in response.json()["detail"]


def test_replace_swaps_the_file_and_delete_removes_it(api, tmp_path):
    client, _, headers, _ = api
    application = start(client, headers[0])
    old = upload(client, headers[0], application["id"], "travel_insurance", make_pdf("Old policy")).json()
    base = f"/applications/{application['id']}/documents"
    response = client.put(f"{base}/{old['id']}", headers=headers[0],
                          files={"file": ("new policy.pdf", make_pdf("New policy"), "application/pdf")})
    assert response.status_code == 200, response.json()
    new = response.json()
    assert new["id"] != old["id"] and new["requirement"] == "travel_insurance" and new["filename"] == "new policy.pdf"
    folder = tmp_path / "passports" / "1" / "documents"
    assert not (folder / f"{old['id']}.pdf").exists() and not (folder / f"{old['id']}.txt").exists()
    assert client.get(f"{base}/{old['id']}/file", headers=headers[0]).status_code == 404
    insurance = next(r for r in checklist(client, headers[0], application["id"])["requirements"]
                     if r["id"] == "travel_insurance")
    assert [d["id"] for d in insurance["documents"]] == [new["id"]]

    assert client.delete(f"{base}/{new['id']}", headers=headers[0]).status_code == 204
    assert not (folder / f"{new['id']}.pdf").exists()
    assert client.delete(f"{base}/{new['id']}", headers=headers[0]).status_code == 404


def test_checklist_is_complete_once_every_required_document_has_a_file(api):
    client, _, headers, _ = api
    application = start(client, headers[0])
    for requirement in ("photo", "travel_insurance", "flight_booking"):
        upload(client, headers[0], application["id"], requirement, make_pdf())
    assert checklist(client, headers[0], application["id"])["complete"] is False
    upload(client, headers[0], application["id"], "bank_statements", make_pdf())
    # The cover letter is optional.
    assert checklist(client, headers[0], application["id"])["complete"] is True


def test_files_for_documents_no_longer_asked_for_are_kept_apart(api):
    client, _, headers, _ = api
    application = start(client, headers[0])
    answer_all(client, headers[0], application["id"], {**ANSWERS, "occupation": "student", "school": "Example U"})
    letter = upload(client, headers[0], application["id"], "enrolment_letter", make_pdf()).json()
    response = client.put(f"/applications/{application['id']}/answers/occupation", headers=headers[0],
                          json={"value": "retired"})
    assert response.status_code == 200
    listed = checklist(client, headers[0], application["id"])
    assert [d["id"] for d in listed["other"]] == [letter["id"]]
    assert "enrolment_letter" not in [r["id"] for r in listed["requirements"]]


def test_archived_applications_are_read_only(api):
    client, _, headers, _ = api
    application = start(client, headers[0])
    document = upload(client, headers[0], application["id"], "photo", make_png()).json()
    client.post(f"/applications/{application['id']}/archive", headers=headers[0])
    assert checklist(client, headers[0], application["id"])["read_only"] is True
    assert upload(client, headers[0], application["id"], "photo", make_png()).status_code == 409
    assert client.delete(f"/applications/{application['id']}/documents/{document['id']}",
                         headers=headers[0]).status_code == 409
    assert client.get(f"/applications/{application['id']}/documents/{document['id']}/file",
                      headers=headers[0]).status_code == 200


def test_deleting_an_application_deletes_its_documents(api, tmp_path):
    client, app, headers, _ = api
    doomed, kept = start(client, headers[0]), start(client, headers[0])
    gone = upload(client, headers[0], doomed["id"], "photo", make_png()).json()
    stays = upload(client, headers[0], kept["id"], "photo", make_png()).json()
    assert client.delete(f"/applications/{doomed['id']}", headers=headers[0]).status_code == 204
    folder = tmp_path / "passports" / "1" / "documents"
    assert not (folder / f"{gone['id']}.jpg").exists() and (folder / f"{stays['id']}.jpg").exists()
    with Session(app.state.engine) as session:
        assert [d.id for d in session.exec(select(SupportingDocument)).all()] == [stays["id"]]


def test_the_routes_are_hidden_while_the_flag_is_off(api, monkeypatch):
    client, app, headers, _ = api
    application = start(client, headers[0])
    monkeypatch.setattr(app.state, "feature_flags", type(app.state.feature_flags)(frozenset()))
    assert client.get(f"/applications/{application['id']}/documents", headers=headers[0]).status_code == 404


def test_text_is_read_from_pdfs_and_images():
    assert read_text(check_document(make_pdf("Name ERIKSSON"))).strip() == "Name ERIKSSON"
    assert read_text(check_document(make_png())) == "OCR TEXT"


@pytest.mark.parametrize("name, expected", [
    ("report.pdf", "report.pdf"), ("C:\\Users\\me\\scan.PNG", "scan.jpg"), ("../../etc/passwd", "passwd.jpg"),
    ("", "document.jpg"), (None, "document.jpg"), ("..", "document.jpg"), ("a\u202eb\nc.pdf", "ab c.jpg"),
    ("x" * 300 + ".pdf", "x" * 100 + ".jpg"),
])
def test_file_names_are_cleaned_for_display(name, expected):
    assert clean_filename(name, expected.rsplit(".", 1)[1]) == expected


def test_print_ready_packet_puts_documents_in_checklist_order_behind_a_cover(api):
    from pypdf import PdfReader
    client, app, headers, _ = api
    application = start(client, headers[0])
    assert upload(client, headers[0], application["id"], "bank_statements", make_pdf("Statement")).status_code == 201
    assert upload(client, headers[0], application["id"], "photo", make_png(), name="me.png").status_code == 201
    url = f"/applications/{application['id']}/documents/packet"
    assert client.get(url, headers=headers[1]).status_code == 404
    response = client.get(url, headers=headers[0])
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["content-disposition"].startswith("attachment")
    pdf = PdfReader(BytesIO(response.content))
    # Cover, then the photo (first on the checklist) and the one-page statement.
    assert len(pdf.pages) == 3
    assert "Statement" in pdf.pages[2].extract_text()
    with Session(app.state.engine) as session:
        purposes = {row.purpose for row in session.exec(select(DocumentAccess)).all()}
    assert "document_packet" in purposes


def test_packet_cover_marks_missing_and_unreadable_documents():
    from pypdf import PdfReader
    from app.documents.packet import PacketItem, build_packet
    broken = PdfWriter()
    broken.add_blank_page(100, 100)
    broken.encrypt("secret")
    encrypted = BytesIO()
    broken.write(encrypted)
    content = build_packet("Japan short-term stay visa", "ANNA MARIA ERIKSSON", "Trip: 1 April 2027 to 9 April 2027", [
        PacketItem("Passport photo", True, []),
        PacketItem("Bank statements", True, [("locked.pdf", "application/pdf", encrypted.getvalue())]),
        PacketItem("Cover letter", False, []),
    ])
    assert len(PdfReader(BytesIO(content)).pages) == 1
