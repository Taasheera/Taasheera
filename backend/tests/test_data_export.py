"""Download all personal data (US 87, CMPS271-121), in step with delete account (CMPS271-117)."""

import io
import json
import zipfile

from sqlmodel import Session, SQLModel, select

from app.agent import queue
from app.agent.storage import AgentFiles
from app.chat_history import save_turn
from app.documents.storage import DocumentStorage
from app.models import (ActivityEvent, Application, DocumentAccess, SupportingDocument, GoogleIdentity, PasswordResetChallenge, PasswordResetToken,
                        RefreshToken, Traveler, TravelerProfile)
from app.personal_data import PERSONAL_DATA, owned_rows
from app.privacy import erase_account
from test_applications_api import ANSWERS, SPECIMEN, answer_all, api, start  # noqa: F401  (pytest fixture)

IMAGE = b"\xff\xd8synthetic specimen image\xff\xd9"
OWNER_TABLES = {"traveler", "application", "authsession"}


PNG = b"\x89PNG\r\n\x1a\n synthetic screenshot"
PDF = b"%PDF-1.4\n% synthetic test document\n%%EOF\n"


def give_everything(app, storage, traveler_id: int, client, headers) -> dict:
    """One traveler with a row in every table that can hold their data, plus a passport file."""
    application_id = start(client, headers)["id"]
    answer_all(client, headers, application_id)  # answers, a status change and its notification
    upload_id = storage.save(traveler_id, IMAGE)
    storage.save_review(traveler_id, upload_id, {"surname": SPECIMEN["surname"], "document_number": SPECIMEN["document_number"]})
    document_id = DocumentStorage.new_id()
    DocumentStorage(storage.root).save(traveler_id, document_id, "pdf", PDF, "Travel insurance")
    with Session(app.state.engine) as session:
        session.add(SupportingDocument(id=document_id, traveler_id=traveler_id, application_id=application_id,
                                       requirement="travel_insurance", filename="insurance.pdf",
                                       content_type="application/pdf", size_bytes=len(PDF)))
        save_turn(session, traveler_id, application_id, "What counts as arrival?", "The day you enter.")
        session.add(TravelerProfile(traveler_id=traveler_id, surname="ERIKSSON", given_names="ANNA MARIA"))
        session.add(GoogleIdentity(subject=f"google-subject-{traveler_id}", traveler_id=traveler_id))
        session.add(RefreshToken(token_hash=f"{traveler_id}" * 64, session_id=f"application-session-{traveler_id}"))
        session.add(PasswordResetChallenge(traveler_id=traveler_id, nonce="n" * 64, code_digest="c" * 64, expires_at=2**31))
        session.add(PasswordResetToken(token_hash=f"r{traveler_id}".ljust(64, "0"), traveler_id=traveler_id, expires_at=2**31))
        session.add(ActivityEvent(traveler_id=traveler_id, event_type="sign_in", identifier="email_password", outcome="success"))
        session.add(DocumentAccess(traveler_id=traveler_id, actor_id=traveler_id, upload_id=upload_id, purpose="review_read"))
        session.commit()
        # One agent run that typed a field and took a screenshot of the page.
        run = queue.enqueue(session, session.get(Application, application_id), "schengen-harmonised")
        assert queue.claim(session).id == run.id
        queue.record_entry(session, run.id, page=1, page_title="Personal details", field="1. Surname", value="ERIKSSON")
        shot_id = queue.record_screenshot(session, run.id, page=1, page_title="Personal details")
        AgentFiles(storage.root).save(traveler_id, application_id, shot_id, PNG)
        queue.finish(session, run.id, "ready", "The form is filled and waiting for you to review and submit it.")
    return {"application_id": application_id, "upload_id": upload_id, "document_id": document_id, "shot_id": shot_id}


def download(client, headers):
    response = client.get("/auth/account/export", headers=headers)
    assert response.status_code == 200, response.text
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    return response, archive, json.loads(archive.read("data.json"))


def test_the_download_is_one_zip_with_every_kind_of_data(api):  # noqa: F811
    client, app, headers, storage = api
    mine = give_everything(app, storage, 1, client, headers[0])
    give_everything(app, storage, 2, client, headers[1])
    with Session(app.state.engine) as session:
        # Staff (traveler 2 here) opened traveler 1's passport; traveler 1 opened traveler 2's.
        session.add(DocumentAccess(traveler_id=1, actor_id=2, upload_id=mine["upload_id"], purpose="image_view"))
        session.add(DocumentAccess(traveler_id=2, actor_id=1, upload_id="b" * 32, purpose="image_view"))
        session.commit()

    response, archive, data = download(client, headers[0])
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["content-disposition"].startswith('attachment; filename="taasheera-data-')
    assert response.headers["cache-control"] == "no-store"
    assert sorted(archive.namelist()) == ["README.txt", f"agent/{mine['application_id']}/{mine['shot_id']}.png",
                                          "data.json", f"documents/{mine['document_id']}.pdf",
                                          f"passport/{mine['upload_id']}.jpg", f"passport/{mine['upload_id']}.json"]
    assert archive.read(f"documents/{mine['document_id']}.pdf") == PDF
    assert data["supportingdocument"][0]["filename"] == "insurance.pdf"
    assert data["agentfieldentry"][0]["value"] == "ERIKSSON" and data["agentrun"][0]["status"] == "ready"
    assert archive.read(f"passport/{mine['upload_id']}.jpg") == IMAGE
    assert json.loads(archive.read(f"passport/{mine['upload_id']}.json"))["surname"] == SPECIMEN["surname"]

    exported = {holding.model.__tablename__ for holding in PERSONAL_DATA if holding.exported}
    assert exported <= set(data)
    for table in exported - {"documentaccess"}:
        assert data[table], f"{table} is empty in the download"
    [account] = data["traveler"]
    assert account["email"] == "traveler1@example.com" and "password_hash" not in account
    assert all("refresh_hash" not in row and "id" not in row for row in data["authsession"])
    assert data["applicationanswer"][0]["application_id"] == mine["application_id"]
    assert {row["question_id"] for row in data["applicationanswer"]} >= {"purpose", "arrival_date"}
    assert [row["content"] for row in data["assistantmessage"]] == ["What counts as arrival?", "The day you enter."]
    assert data["travelerprofile"][0]["surname"] == "ERIKSSON"
    assert data["notification"][0]["kind"] == "status_changed"
    # Who opened my passport: staff are not named, and my staff work on someone else is not mine to download.
    assert {row["accessed_by"] for row in data["documentaccess"]} >= {"Authorized staff"}
    assert all(row["traveler_id"] == 1 and "actor_id" not in row for row in data["documentaccess"])
    secrets = {"password_hash", "refresh_hash", "token_hash", "code_digest", "nonce"}
    assert not secrets & set(json.dumps(data).replace('"', " ").split())


def test_nothing_from_another_traveler_is_in_the_download(api):  # noqa: F811
    client, app, headers, storage = api
    theirs = give_everything(app, storage, 2, client, headers[1])
    give_everything(app, storage, 1, client, headers[0])
    _, archive, data = download(client, headers[0])
    text = archive.read("data.json").decode()
    assert "traveler2@example.com" not in text and theirs["application_id"] not in text
    assert not [name for name in archive.namelist() if theirs["upload_id"] in name]
    assert {row["traveler_id"] for table in data.values() if isinstance(table, list)
            for row in table if "traveler_id" in row} == {1}


def test_downloading_is_recorded_and_the_file_reads_show_in_access_history(api):  # noqa: F811
    client, app, headers, storage = api
    mine = give_everything(app, storage, 1, client, headers[0])
    download(client, headers[0])
    with Session(app.state.engine) as session:
        assert session.exec(select(ActivityEvent).where(ActivityEvent.traveler_id == 1,
                                                        ActivityEvent.event_type == "data_export")).one()
        reads = session.exec(select(DocumentAccess).where(DocumentAccess.purpose == "data_export")).all()
    # The passport image and its review, then the supporting document.
    assert [(row.traveler_id, row.actor_id, row.upload_id) for row in reads] == (
        [(1, 1, mine["upload_id"])] * 2 + [(1, 1, mine["document_id"]), (1, 1, mine["shot_id"])])


def test_signed_out_requests_and_too_many_downloads_are_refused(api):  # noqa: F811
    client, _, headers, _ = api
    assert client.get("/auth/account/export").status_code == 401
    for _ in range(5):
        assert client.get("/auth/account/export", headers=headers[0]).status_code == 200
    assert client.get("/auth/account/export", headers=headers[0]).status_code == 429


def test_every_table_about_a_traveler_is_listed_for_download_and_deletion():
    listed = {holding.model.__tablename__ for holding in PERSONAL_DATA}
    refers_to_owner = {
        table.name for table in SQLModel.metadata.sorted_tables
        if table.name == "traveler" or any(key.column.table.name in OWNER_TABLES for key in table.foreign_keys)
    }
    assert refers_to_owner - listed == set(), "List these tables in app/personal_data.py and erase them"
    assert all(holding.exported or holding.reason for holding in PERSONAL_DATA), "Say why a table is left out"


def test_delete_account_removes_every_listed_table_and_keeps_other_travelers(api):  # noqa: F811
    client, app, headers, storage = api
    give_everything(app, storage, 1, client, headers[0])
    give_everything(app, storage, 2, client, headers[1])
    with Session(app.state.engine) as session:
        for holding in PERSONAL_DATA:
            if holding.model.__tablename__ != "accounterasure":  # only exists during a deletion
                assert owned_rows(session, holding, 1), f"test setup left {holding.model.__tablename__} empty"
        assert erase_account(session, session.get(Traveler, 1), app.state.passport_storage,
                             app.state.activity_directory, app.state.auth_settings.jwt_secret)
    with Session(app.state.engine) as session:
        left = {holding.model.__tablename__: len(owned_rows(session, holding, 1)) for holding in PERSONAL_DATA}
        assert {table: count for table, count in left.items() if count} == {}
        kept = {holding.model.__tablename__ for holding in PERSONAL_DATA if owned_rows(session, holding, 2)}
        assert kept >= {"traveler", "application", "applicationanswer", "notification", "assistantmessage"}
