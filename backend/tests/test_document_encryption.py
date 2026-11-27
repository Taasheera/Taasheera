"""Traveler files are encrypted at rest, bound to their owner and id (CMPS271-114)."""

import base64
import io
import logging
import os
import secrets
import shlex
import shutil
import subprocess
import sys
import zipfile
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

import manage
from app.agent.storage import AgentFiles
from app.documents.storage import DocumentStorage
from app.file_encryption import MAGIC, FileCipher, FileDecryptionError, encrypt_existing_files
from app.main import create_app
from app.passport.storage import PassportImageStorage
from test_applications_api import SPECIMEN, api, start  # noqa: F401 (api is a fixture)
from test_supporting_documents import make_pdf

PNG = b"\x89PNG\r\n\x1a\n synthetic screenshot"


def new_key() -> str:
    return base64.b64encode(secrets.token_bytes(32)).decode()


def cipher() -> FileCipher:
    return FileCipher(base64.b64decode(new_key()))


def jpeg() -> bytes:
    out = BytesIO()
    specimen = Image.new("RGB", (800, 500), "white")
    ImageDraw.Draw(specimen).rectangle((100, 120, 700, 380), fill="black")
    specimen.save(out, format="JPEG")
    return out.getvalue()


@pytest.fixture
def key(monkeypatch):
    value = new_key()
    monkeypatch.setenv("DOCUMENT_ENCRYPTION_KEY", value)
    monkeypatch.setenv("FEATURE_PRIVACY_CONSENT", "false")
    monkeypatch.setattr("app.documents.files.pytesseract.image_to_string", lambda image, timeout: "OCR TEXT")
    return value


def test_without_a_key_files_stay_as_they_were_and_startup_warns_once(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="app.file_encryption"):
        app = create_app(f"sqlite:///{(tmp_path / 'app.db').as_posix()}")
    warnings = [record.getMessage() for record in caplog.records if record.name == "app.file_encryption"]
    assert len(warnings) == 1 and "DOCUMENT_ENCRYPTION_KEY is not set" in warnings[0]
    assert app.state.passport_storage.cipher is None
    storage = PassportImageStorage(tmp_path / "plain")
    upload_id = storage.save(1, b"\xff\xd8 plain image")
    assert (tmp_path / "plain" / "1" / f"{upload_id}.jpg").read_bytes() == b"\xff\xd8 plain image"


@pytest.mark.parametrize("value", ["too-short", base64.b64encode(bytes(16)).decode(),
                                   base64.b64encode(bytes(33)).decode(), "not base64 at all!" * 3, "é" * 44])
def test_a_malformed_key_stops_startup(tmp_path, monkeypatch, value):
    monkeypatch.setenv("DOCUMENT_ENCRYPTION_KEY", value)
    with pytest.raises(RuntimeError, match="DOCUMENT_ENCRYPTION_KEY must be 32 random bytes"):
        create_app(f"sqlite:///{(tmp_path / 'app.db').as_posix()}")


def test_passport_images_and_confirmed_details_are_encrypted_on_disk(key, api, tmp_path):  # noqa: F811
    client, app, headers, _ = api
    image = jpeg()
    upload_id = client.post("/passports", headers=headers[0],
                            files={"file": ("passport.jpg", image, "image/jpeg")}).json()["id"]
    raw = (tmp_path / "passports" / "1" / f"{upload_id}.jpg").read_bytes()
    assert raw.startswith(MAGIC) and b"JFIF" not in raw

    shown = client.get(f"/passports/{upload_id}/image", headers=headers[0])
    assert shown.status_code == 200 and shown.content.startswith(b"\xff\xd8\xff")
    assert client.put(f"/passports/{upload_id}/review", headers=headers[0], json=SPECIMEN).status_code == 200
    review = (tmp_path / "passports" / "1" / f"{upload_id}.json").read_bytes()
    assert review.startswith(MAGIC) and b"ERIKSSON" not in review and b"L898902C3" not in review
    assert client.get(f"/passports/{upload_id}/review", headers=headers[0]).json()["surname"] == "ERIKSSON"
    assert app.state.passport_storage.latest_review(1)["document_number"] == "L898902C3"
    assert client.get(f"/passports/{upload_id}/image", headers=headers[1]).status_code == 404

    archive = zipfile.ZipFile(io.BytesIO(client.get("/auth/account/export", headers=headers[0]).content))
    assert archive.read(f"passport/{upload_id}.jpg") == shown.content


def test_supporting_documents_and_their_text_are_encrypted_and_pdfs_come_back_as_uploaded(key, api, tmp_path):  # noqa: F811
    client, _, headers, _ = api
    application = start(client, headers[0])
    statement = make_pdf("Statement for ANNA MARIA ERIKSSON")
    base = f"/applications/{application['id']}/documents"
    document = client.post(base, headers=headers[0], data={"requirement": "bank_statements"},
                           files={"file": ("statement.pdf", statement, "application/pdf")}).json()
    assert document["size_bytes"] == len(statement)
    folder = tmp_path / "passports" / "1" / "documents"
    for name in (f"{document['id']}.pdf", f"{document['id']}.txt"):
        raw = (folder / name).read_bytes()
        assert raw.startswith(MAGIC) and b"ERIKSSON" not in raw
    assert client.get(f"{base}/{document['id']}/file", headers=headers[0]).content == statement

    out = BytesIO()
    Image.new("RGB", (40, 30), "white").save(out, format="PNG")
    photo = client.post(base, headers=headers[0], data={"requirement": "photo"},
                        files={"file": ("me.png", out.getvalue(), "image/png")}).json()
    assert (folder / f"{photo['id']}.jpg").read_bytes().startswith(MAGIC)
    assert client.get(f"{base}/{photo['id']}/file", headers=headers[0]).content.startswith(b"\xff\xd8\xff")

    packet = client.get(f"{base}/packet", headers=headers[0])
    assert packet.status_code == 200 and packet.content.startswith(b"%PDF-")
    assert client.get(f"{base}/crosscheck", headers=headers[0]).status_code == 200


def test_agent_screenshots_are_encrypted(tmp_path):
    files = AgentFiles(tmp_path, cipher=cipher())
    application_id, shot_id = "a" * 32, "b" * 32
    files.save(7, application_id, shot_id, PNG)
    assert (tmp_path / "7" / "agent" / application_id / f"{shot_id}.png").read_bytes().startswith(MAGIC)
    assert files.load(7, application_id, shot_id) == PNG


def test_files_written_before_the_key_still_open_once_it_is_set(tmp_path):
    plain = PassportImageStorage(tmp_path)
    upload_id = plain.save(1, b"\xff\xd8 older image")
    plain.save_review(1, upload_id, {"surname": "ERIKSSON"})
    DocumentStorage(tmp_path).save(1, "c" * 32, "pdf", b"%PDF-1.4 older", "older text")
    AgentFiles(tmp_path).save(1, "a" * 32, "b" * 32, PNG)

    key = cipher()
    keyed = PassportImageStorage(tmp_path, cipher=key)
    assert keyed.load(1, upload_id) == b"\xff\xd8 older image"
    assert keyed.load_review(1, upload_id) == {"surname": "ERIKSSON"}
    documents = DocumentStorage(tmp_path, cipher=key)
    assert documents.load(1, "c" * 32, "pdf", purpose="document_view") == b"%PDF-1.4 older"
    assert documents.load_text(1, "c" * 32, purpose="consistency_check") == "older text"
    assert AgentFiles(tmp_path, cipher=key).load(1, "a" * 32, "b" * 32) == PNG

    newer = keyed.save(1, b"\xff\xd8 newer image")
    assert keyed.load(1, newer) == b"\xff\xd8 newer image"
    assert (tmp_path / "1" / f"{newer}.jpg").read_bytes().startswith(MAGIC)


def test_a_file_copied_into_another_travelers_folder_or_renamed_does_not_decrypt(tmp_path):
    storage = PassportImageStorage(tmp_path, cipher=cipher())
    upload_id = storage.save(1, b"\xff\xd8 owner one's passport")
    (tmp_path / "2").mkdir()
    shutil.copy(tmp_path / "1" / f"{upload_id}.jpg", tmp_path / "2" / f"{upload_id}.jpg")
    with pytest.raises(FileDecryptionError):
        storage.load(2, upload_id)
    other_id = "d" * 32
    shutil.copy(tmp_path / "1" / f"{upload_id}.jpg", tmp_path / "1" / f"{other_id}.jpg")
    with pytest.raises(FileDecryptionError):
        storage.load(1, other_id)

    documents = DocumentStorage(tmp_path, cipher=storage.cipher)
    documents.save(1, "e" * 32, "pdf", b"%PDF-1.4 statement", "statement text")
    (tmp_path / "2" / "documents").mkdir()
    for extension in ("pdf", "txt"):
        shutil.copy(tmp_path / "1" / "documents" / f"{'e' * 32}.{extension}",
                    tmp_path / "2" / "documents" / f"{'e' * 32}.{extension}")
    with pytest.raises(FileDecryptionError):
        documents.load(2, "e" * 32, "pdf", purpose="document_view")
    with pytest.raises(FileDecryptionError):
        documents.load_text(2, "e" * 32, purpose="consistency_check")
    assert storage.load(1, upload_id) == b"\xff\xd8 owner one's passport"


def test_an_encrypted_file_needs_the_same_key_and_an_unaltered_file(tmp_path):
    storage = PassportImageStorage(tmp_path, cipher=cipher())
    upload_id = storage.save(1, b"\xff\xd8 image")
    for other in (PassportImageStorage(tmp_path), PassportImageStorage(tmp_path, cipher=cipher())):
        with pytest.raises(FileDecryptionError):
            other.load(1, upload_id)
    path = tmp_path / "1" / f"{upload_id}.jpg"
    original = path.read_bytes()
    path.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
    with pytest.raises(FileDecryptionError):
        storage.load(1, upload_id)
    path.write_bytes(MAGIC + b"\x02" + original[len(MAGIC) + 1:])
    with pytest.raises(FileDecryptionError, match="version"):
        storage.load(1, upload_id)


def plaintext_files(root):
    """One of every kind of traveler file, written before the key, and something that isn't ours."""
    passports = PassportImageStorage(root)
    first = passports.save(1, b"\xff\xd8 first image")
    passports.save_review(1, first, {"surname": "FIRST"})
    second = passports.save(1, b"\xff\xd8 second image")
    passports.save_review(1, second, {"surname": "SECOND"})
    older = (root / "1" / f"{second}.json").stat().st_mtime_ns - 10**9
    os.utime(root / "1" / f"{second}.json", ns=(older, older))
    DocumentStorage(root).save(1, "c" * 32, "pdf", b"%PDF-1.4 statement", "statement text")
    AgentFiles(root).save(2, "a" * 32, "b" * 32, PNG)
    (root / "1" / "notes.txt").write_bytes(b"not a traveler file")
    return first, second


def test_the_command_encrypts_existing_files_in_place_once(tmp_path, monkeypatch, capsys):
    root = tmp_path / "passports"
    first, second = plaintext_files(root)
    times = {path: path.stat().st_mtime_ns for path in root.rglob("*") if path.is_file()}
    value = new_key()
    monkeypatch.setenv("DOCUMENT_ENCRYPTION_KEY", value)

    manage.main(["encrypt-documents"])
    output = capsys.readouterr().out
    assert output.strip() == "Encrypted 7 files; 0 were already encrypted."
    assert first not in output and second not in output and "c" * 32 not in output

    key = FileCipher(base64.b64decode(value))
    passports = PassportImageStorage(root, cipher=key)
    assert passports.load(1, first) == b"\xff\xd8 first image"
    assert passports.latest_review(1) == {"surname": "FIRST"}
    documents = DocumentStorage(root, cipher=key)
    assert documents.load(1, "c" * 32, "pdf", purpose="document_view") == b"%PDF-1.4 statement"
    assert documents.load_text(1, "c" * 32, purpose="consistency_check") == "statement text"
    assert AgentFiles(root, cipher=key).load(2, "a" * 32, "b" * 32) == PNG
    for path, mtime in times.items():
        assert path.stat().st_mtime_ns == mtime
        assert path.read_bytes().startswith(MAGIC) == (path.name != "notes.txt")
    assert sorted(path.name for path in root.rglob("*.tmp")) == []

    manage.main(["encrypt-documents"])
    assert capsys.readouterr().out.strip() == "Encrypted 0 files; 7 were already encrypted."


def test_the_command_reports_files_another_key_cannot_open(tmp_path, monkeypatch):
    root = tmp_path / "passports"
    plaintext_files(root)
    monkeypatch.setenv("DOCUMENT_ENCRYPTION_KEY", new_key())
    manage.main(["encrypt-documents"])
    before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    monkeypatch.setenv("DOCUMENT_ENCRYPTION_KEY", new_key())
    with pytest.raises(SystemExit, match="Encrypted 0 files; 0 were already encrypted. "
                                         "7 encrypted files do not open with this key."):
        manage.main(["encrypt-documents"])
    assert {path: path.read_bytes() for path in root.rglob("*") if path.is_file()} == before


def test_the_command_needs_a_key_and_a_storage_folder(tmp_path, monkeypatch):
    with pytest.raises(SystemExit, match="Set DOCUMENT_ENCRYPTION_KEY first"):
        manage.main(["encrypt-documents"])
    monkeypatch.setenv("DOCUMENT_ENCRYPTION_KEY", new_key())
    monkeypatch.setenv("PASSPORT_STORAGE_DIR", str(tmp_path / "missing"))
    with pytest.raises(SystemExit, match="No storage folder"):
        manage.main(["encrypt-documents"])
    assert not (tmp_path / "missing").exists()


@pytest.mark.skipif(os.name == "nt", reason="runs the host's bash setup step")
def test_a_new_host_gets_one_document_key_that_later_setups_keep(tmp_path):
    repository = Path(__file__).resolve().parents[2]
    source = (repository / "deploy" / "aws" / "bootstrap.sh").read_text()
    start = source.index("if [ ! -f /srv/taasheera/private/app.env ]; then")
    end = source.index("ln -sfn /srv/taasheera/private/app.env", start)
    script = source[start:end].replace("/srv/taasheera/private", str(tmp_path)).replace("python3", shlex.quote(sys.executable))
    environment = {**os.environ, "TAASHEERA_SITE_URL": "https://example.cloudfront.net",
                   "TAASHEERA_ORIGIN_TOKEN": "f" * 64}

    def settings() -> dict[str, str]:
        subprocess.run(["bash", "-ec", script], check=True, cwd=repository, env=environment)
        return dict(line.split("=", 1) for line in (tmp_path / "app.env").read_text().splitlines())

    first = settings()
    assert len(base64.b64decode(first["DOCUMENT_ENCRYPTION_KEY"], validate=True)) == 32
    again = settings()
    assert again["DOCUMENT_ENCRYPTION_KEY"] == first["DOCUMENT_ENCRYPTION_KEY"]
    assert again["JWT_SECRET"] == first["JWT_SECRET"]


def test_a_busy_owner_is_left_for_the_next_run(tmp_path):
    root = tmp_path / "passports"
    plaintext_files(root)

    class Lock:
        def __init__(self, owner_id):
            self.owner_id = owner_id

        def __enter__(self):
            if self.owner_id == 2:
                raise TimeoutError
            return self

        def __exit__(self, *exc):
            return False

    counts = encrypt_existing_files(root, cipher(), Lock)
    assert counts == {"encrypted": 6, "already_encrypted": 0, "unreadable": 0, "busy_owners": 1}
    assert (root / "2" / "agent" / ("a" * 32) / f"{'b' * 32}.png").read_bytes() == PNG
