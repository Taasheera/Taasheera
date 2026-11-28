import io
import json
import re
import zipfile
from pathlib import Path

import pytest
from sqlmodel import Session, select

from app.models import ActivityEvent, DocumentAccess, SupportingDocument
from test_applications_api import answer_all, api, start  # noqa: F401
from test_supporting_documents import checklist, consent_not_required, make_pdf, make_png, no_ocr, upload  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]


def vault(client, headers):
    response = client.get("/vault/documents", headers=headers)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "private, no-store"
    return response.json()


def attach(client, headers, document_id, application_id, requirement="bank_statements"):
    return client.post(f"/vault/documents/{document_id}/attachments", headers=headers,
                       json={"application_id": application_id, "requirement": requirement})


def files(tmp_path):
    folder = tmp_path / "passports" / "1" / "documents"
    return sorted(path.name for path in folder.iterdir()) if folder.is_dir() else []


def ids_in(client, headers, application_id, requirement="bank_statements"):
    listed = checklist(client, headers, application_id)
    return [d["id"] for r in listed["requirements"] if r["id"] == requirement for d in r["documents"]]


@pytest.fixture
def two(api):
    client, app, headers, _ = api
    first, second = start(client, headers[0]), start(client, headers[0])
    document = upload(client, headers[0], first["id"], "bank_statements", make_pdf("Statement")).json()
    return first["id"], second["id"], document


def test_the_vault_lists_each_file_with_where_it_is_used(api, two):
    client, _, headers, _ = api
    first, second, document = two
    body = vault(client, headers[0])
    [listed] = body["documents"]
    assert listed["id"] == document["id"] and listed["label"] == "Bank statements"
    assert listed["filename"] == "statement.pdf" and listed["content_type"] == "application/pdf"
    assert [(use["application_id"], use["requirement"]) for use in listed["uses"]] == [(first, "bank_statements")]
    assert listed["uses"][0]["application_title"] == "Schengen short-stay visa (type C)"
    assert [language["code"] for language in listed["languages"]] == ["en"]
    targets = {target["application_id"]: target for target in body["targets"]}
    assert set(targets) == {first, second}
    assert "bank_statements" in [r["id"] for r in targets[second]["requirements"]]
    assert vault(client, headers[1]) == {"documents": [], "targets": []}


def test_attaching_reuses_the_file_without_uploading_it_again(api, two, tmp_path):
    client, app, headers, _ = api
    first, second, document = two
    before = files(tmp_path)
    response = attach(client, headers[0], document["id"], second)
    assert response.status_code == 201, response.json()
    use = response.json()
    assert use["application_id"] == second and use["requirement_label"] == "Bank statements" and use["id"] != document["id"]
    assert files(tmp_path) == before
    assert ids_in(client, headers[0], second) == [use["id"]]
    downloaded = client.get(f"/applications/{second}/documents/{use['id']}/file", headers=headers[0])
    assert downloaded.status_code == 200 and downloaded.content.startswith(b"%PDF-")
    [listed] = vault(client, headers[0])["documents"]
    assert [u["application_id"] for u in listed["uses"]] == [first, second]
    with Session(app.state.engine) as session:
        assert session.exec(select(ActivityEvent).where(ActivityEvent.event_type == "document_attach")).one()


def test_attaching_is_refused_when_it_would_not_make_sense(api, two):
    client, _, headers, _ = api
    first, second, document = two
    assert attach(client, headers[0], document["id"], second).status_code == 201
    response = attach(client, headers[0], document["id"], second)
    assert response.status_code == 409 and "already attached" in response.json()["detail"]
    response = attach(client, headers[0], document["id"], second, "employment_letter")
    assert response.status_code == 422 and "doesn't ask for that document" in response.json()["detail"]
    other = start(client, headers[1])["id"]
    assert attach(client, headers[0], document["id"], other).status_code == 404
    assert attach(client, headers[1], document["id"], other).status_code == 404
    assert attach(client, headers[0], "f" * 32, second).status_code == 404
    assert attach(client, headers[0], "not-an-id", second).status_code == 404
    response = client.post(f"/vault/documents/{document['id']}/attachments", headers=headers[0],
                           json={"application_id": second, "requirement": "photo", "traveler_id": 2})
    assert response.status_code == 422
    client.post(f"/applications/{first}/archive", headers=headers[0])
    response = attach(client, headers[0], document["id"], first, "photo")
    assert response.status_code == 409 and "archived" in response.json()["detail"]


def test_the_per_document_limit_counts_attached_files(api, two):
    client, _, headers, _ = api
    _, second, document = two
    for _ in range(5):
        assert upload(client, headers[0], second, "bank_statements", make_pdf()).status_code == 201
    response = attach(client, headers[0], document["id"], second)
    assert response.status_code == 409 and "up to 5 files" in response.json()["detail"]


def test_removing_a_file_from_one_application_keeps_it_for_the_other(api, two, tmp_path):
    client, _, headers, _ = api
    first, second, document = two
    use = attach(client, headers[0], document["id"], second).json()
    assert client.delete(f"/applications/{first}/documents/{document['id']}", headers=headers[0]).status_code == 204
    assert f"{document['id']}.pdf" in files(tmp_path)
    assert client.get(f"/applications/{second}/documents/{use['id']}/file", headers=headers[0]).status_code == 200
    [listed] = vault(client, headers[0])["documents"]
    assert listed["id"] == document["id"] and [u["application_id"] for u in listed["uses"]] == [second]
    assert client.delete(f"/applications/{second}/documents/{use['id']}", headers=headers[0]).status_code == 204
    assert files(tmp_path) == []


def test_replacing_a_shared_file_in_one_application_leaves_the_other_alone(api, two, tmp_path):
    client, _, headers, _ = api
    first, second, document = two
    use = attach(client, headers[0], document["id"], second).json()
    response = client.put(f"/applications/{second}/documents/{use['id']}", headers=headers[0],
                          files={"file": ("new.pdf", make_pdf("New statement"), "application/pdf")})
    assert response.status_code == 200
    new = response.json()
    assert f"{document['id']}.pdf" in files(tmp_path) and f"{new['id']}.pdf" in files(tmp_path)
    assert ids_in(client, headers[0], first) == [document["id"]]
    assert len(vault(client, headers[0])["documents"]) == 2


def test_deleting_an_application_keeps_documents_another_application_uses(api, two, tmp_path):
    client, app, headers, _ = api
    first, second, document = two
    use = attach(client, headers[0], document["id"], second).json()
    photo = upload(client, headers[0], first, "photo", make_png()).json()
    assert client.delete(f"/applications/{first}", headers=headers[0]).status_code == 204
    assert f"{document['id']}.pdf" in files(tmp_path) and f"{photo['id']}.jpg" not in files(tmp_path)
    assert client.get(f"/applications/{second}/documents/{use['id']}/file", headers=headers[0]).status_code == 200
    assert client.delete(f"/applications/{second}", headers=headers[0]).status_code == 204
    assert files(tmp_path) == []
    with Session(app.state.engine) as session:
        assert session.exec(select(SupportingDocument)).all() == []


def test_deleting_from_the_vault_detaches_everywhere_and_is_audited(api, two, tmp_path):
    client, app, headers, _ = api
    first, second, document = two
    answer_all(client, headers[0], second)
    attach(client, headers[0], document["id"], second)
    client.post(f"/applications/{first}/archive", headers=headers[0])

    def missing(application_id):
        issues = client.get(f"/applications/{application_id}/check", headers=headers[0]).json()["issues"]
        return "Not uploaded yet: Bank statements" in [issue["message"] for issue in issues]

    assert not missing(second)
    assert client.delete(f"/vault/documents/{document['id']}", headers=headers[1]).status_code == 404
    assert f"{document['id']}.pdf" in files(tmp_path)
    response = client.delete(f"/vault/documents/{document['id']}", headers=headers[0])
    assert response.status_code == 204 and response.headers["cache-control"] == "private, no-store"
    assert files(tmp_path) == []
    assert ids_in(client, headers[0], first) == [] and ids_in(client, headers[0], second) == []
    assert missing(second)
    assert vault(client, headers[0])["documents"] == []
    assert client.delete(f"/vault/documents/{document['id']}", headers=headers[0]).status_code == 404
    with Session(app.state.engine) as session:
        [access] = session.exec(select(DocumentAccess).where(DocumentAccess.purpose == "document_delete")).all()
        assert (access.traveler_id, access.actor_id, access.upload_id) == (1, 1, document["id"])
        assert session.exec(select(ActivityEvent).where(ActivityEvent.event_type == "document_vault_delete")).one()


def test_rows_from_before_the_vault_still_name_their_own_file(api, tmp_path):
    client, app, headers, _ = api
    application = start(client, headers[0])["id"]
    document = upload(client, headers[0], application, "bank_statements", make_pdf("Statement")).json()
    with Session(app.state.engine) as session:
        row = session.get(SupportingDocument, document["id"])
        row.file_id = None
        session.add(row)
        session.commit()
    [listed] = vault(client, headers[0])["documents"]
    assert listed["id"] == document["id"]
    assert client.delete(f"/vault/documents/{document['id']}", headers=headers[0]).status_code == 204
    assert files(tmp_path) == []


def test_the_data_download_holds_a_shared_file_once(api, two):
    client, _, headers, _ = api
    _, second, document = two
    attach(client, headers[0], document["id"], second)
    response = client.get("/auth/account/export", headers=headers[0])
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    assert [name for name in archive.namelist() if name.startswith("documents/")] == [f"documents/{document['id']}.pdf"]
    rows = json.loads(archive.read("data.json"))["supportingdocument"]
    assert len(rows) == 2 and {row["file_id"] for row in rows} == {document["id"]}


def test_the_vault_is_hidden_while_its_flags_are_off(api, two, monkeypatch):
    client, app, headers, _ = api
    _, second, document = two
    flags = type(app.state.feature_flags)
    for enabled in ({"supporting_documents"}, {"document_vault"}):
        monkeypatch.setattr(app.state, "feature_flags", flags(frozenset(enabled)))
        assert client.get("/vault/documents", headers=headers[0]).status_code == 404
        assert attach(client, headers[0], document["id"], second).status_code == 404
        assert client.delete(f"/vault/documents/{document['id']}", headers=headers[0]).status_code == 404


def test_the_vault_prefix_is_proxied_and_is_not_a_page():
    assert re.search(r"\|vault\|", (ROOT / "deploy/aws/nginx.conf").read_text(encoding="utf-8"))
    assert "'vault'" in (ROOT / "frontend/vite.config.ts").read_text(encoding="utf-8")
    assert "'vault'" in (ROOT / "frontend/scripts/write-netlify-redirects.mjs").read_text(encoding="utf-8")
    assert "path: 'vault'" not in (ROOT / "frontend/src/routes.tsx").read_text(encoding="utf-8")
