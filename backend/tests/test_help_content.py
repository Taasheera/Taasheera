"""FAQ and help content that admins edit without a deploy (CMPS271-129)."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text
from sqlmodel import Session, select

from app.auth import access_token
from app.database import build_engine
from app.main import create_app
from app.models import ActivityEvent, AuthSession, FaqEntry, Traveler
from app.schema import migration_config

ROOT = Path(__file__).resolve().parents[2]
PEOPLE = {"traveler": "traveler", "support": "support", "admin": "admin"}
ENTRY = {"question": "Can I pay later?", "answer": "Yes.\n\n- **Before** you submit.", "category": "Payments",
         "language": "en", "published": True}


@pytest.fixture
def api(tmp_path):
    app = create_app(f"sqlite:///{(tmp_path / 'help.db').as_posix()}")
    with TestClient(app) as client:
        ids, headers = {}, {}
        with Session(app.state.engine) as session:
            for name, role in PEOPLE.items():
                traveler = Traveler(name=name.title(), email=f"{name}@example.com", password_hash="test-hash", role=role)
                session.add(traveler)
                session.flush()
                auth_session = AuthSession(id=f"help-session-{name}", traveler_id=traveler.id,
                                           refresh_hash=name.ljust(64, "0"), expires_at=2**31)
                session.add(auth_session)
                ids[name] = traveler.id
                headers[name] = {"Authorization": f"Bearer {access_token(auth_session, app.state.auth_settings).access_token}"}
            session.commit()
        yield SimpleNamespace(client=client, app=app, ids=ids, headers=headers)


def add(api, **changes):
    response = api.client.post("/admin/help-content", headers=api.headers["admin"], json={**ENTRY, **changes})
    assert response.status_code == 200, response.text
    return response.json()


def public(api, language="en"):
    response = api.client.get(f"/help-content?language={language}")
    assert response.status_code == 200
    return response.json()


def test_the_public_list_shows_published_entries_only_in_order_without_sign_in(api):
    first = add(api, question="First?")
    add(api, question="Hidden?", published=False)
    third = add(api, question="Third?")
    add(api, question="عربي؟", answer="نعم.", language="ar")
    response = api.client.get("/help-content")
    assert response.headers["cache-control"] == "no-cache"
    assert [entry["question"] for entry in response.json()] == ["First?", "Third?"]
    assert set(response.json()[0]) == {"id", "question", "answer", "category", "language"}
    assert [entry["question"] for entry in public(api, "ar")] == ["عربي؟"]
    assert api.client.get("/help-content?language=fr").status_code == 422
    assert [first["position"], third["position"]] == [1, 3]


def test_admins_add_edit_publish_reorder_and_delete(api):
    admin = api.headers["admin"]
    one, two, three = add(api, question="One?"), add(api, question="Two?"), add(api, question="Three?")
    assert one["updated_by_name"] == "Admin" and one["published"] is True

    hidden = api.client.put(f"/admin/help-content/{two['id']}", headers=admin, json={**ENTRY, "question": "Two?", "published": False})
    assert hidden.status_code == 200 and hidden.json()["published"] is False
    assert [entry["question"] for entry in public(api)] == ["One?", "Three?"]

    order = api.client.put("/admin/help-content/order", headers=admin, json={"language": "en", "ids": [three["id"], one["id"], two["id"]]})
    assert order.status_code == 200
    assert [(entry["question"], entry["position"]) for entry in order.json()] == [("Three?", 1), ("One?", 2), ("Two?", 3)]
    assert [entry["question"] for entry in public(api)] == ["Three?", "One?"]

    assert api.client.delete(f"/admin/help-content/{three['id']}", headers=admin).status_code == 204
    assert api.client.delete(f"/admin/help-content/{three['id']}", headers=admin).status_code == 404
    assert [entry["question"] for entry in api.client.get("/admin/help-content", headers=admin).json()] == ["One?", "Two?"]
    with Session(api.app.state.engine) as session:
        changes = session.exec(select(ActivityEvent.identifier).where(ActivityEvent.event_type == "help_content")).all()
    assert changes == ["create", "create", "create", "update", "reorder", "delete"]


def test_moving_an_entry_to_another_language_puts_it_last_there(api):
    add(api, question="Arabic one", language="ar")
    english = add(api, question="Moves")
    moved = api.client.put(f"/admin/help-content/{english['id']}", headers=api.headers["admin"],
                           json={**ENTRY, "question": "Moves", "language": "ar"}).json()
    assert (moved["language"], moved["position"]) == ("ar", 2)
    assert [entry["question"] for entry in public(api, "ar")] == ["Arabic one", "Moves"]


def test_a_reorder_must_list_every_entry_of_the_language_once(api):
    one, two = add(api, question="One?"), add(api, question="Two?")
    arabic = add(api, question="Ar?", language="ar")
    for ids in ([one["id"]], [one["id"], two["id"], two["id"]], [one["id"], two["id"], arabic["id"]], [one["id"], 999]):
        response = api.client.put("/admin/help-content/order", headers=api.headers["admin"], json={"language": "en", "ids": ids})
        assert response.status_code in {409, 422}, ids
    assert [entry["position"] for entry in api.client.get("/admin/help-content?language=en", headers=api.headers["admin"]).json()] == [1, 2]


@pytest.mark.parametrize("changes", [{"question": "   "}, {"answer": ""}, {"category": ""}, {"question": "x" * 301},
                                     {"answer": "x" * 4001}, {"category": "x" * 61}, {"language": "fr"},
                                     {"answer": "Hi\x00there"}, {"updated_by": 1}, {"position": 1}])
def test_entries_are_validated(api, changes):
    response = api.client.post("/admin/help-content", headers=api.headers["admin"], json={**ENTRY, **changes})
    assert response.status_code == 422


def test_text_is_stored_trimmed_with_plain_line_endings(api):
    entry = add(api, question="  Spaced?  ", answer="  Line one\r\nLine two  ", category=" General ")
    assert (entry["question"], entry["answer"], entry["category"]) == ("Spaced?", "Line one\nLine two", "General")


@pytest.mark.parametrize("who,expected", [("traveler", 403), ("support", 403), (None, 401)])
def test_only_admins_manage_help_content(api, who, expected):
    entry = add(api)
    headers = api.headers[who] if who else {}
    for method, path, body in [("GET", "/admin/help-content", None), ("POST", "/admin/help-content", ENTRY),
                               ("PUT", f"/admin/help-content/{entry['id']}", ENTRY),
                               ("PUT", "/admin/help-content/order", {"language": "en", "ids": [entry["id"]]}),
                               ("DELETE", f"/admin/help-content/{entry['id']}", None)]:
        assert api.client.request(method, path, headers=headers, json=body).status_code == expected, (method, path)
    assert [item["question"] for item in public(api)] == [ENTRY["question"]]


def test_the_migration_publishes_the_faq_the_site_showed_before(tmp_path, monkeypatch):
    url = f"sqlite:///{(tmp_path / 'seeded.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    command.upgrade(migration_config(), "head")
    engine = build_engine(url)
    with Session(engine) as session:
        entries = session.exec(select(FaqEntry).order_by(FaqEntry.position)).all()
    content = (ROOT / "frontend/src/content.ts").read_text(encoding="utf-8").replace("\\'", "'")
    assert len(entries) == 10
    assert all(entry.published and entry.language == "en" and entry.updated_by is None for entry in entries)
    assert [entry.position for entry in entries] == list(range(1, 11))
    for entry in entries:
        assert f"question: '{entry.question}'" in content
        assert entry.answer in content
    command.downgrade(migration_config(), "f6a2d8c4e317")
    with engine.connect() as connection:
        assert not {"faqentry", "supportrequest", "supportaccessgrant"} & set(inspect(connection).get_table_names())
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar() == "f6a2d8c4e317"
    engine.dispose()


def test_new_api_prefixes_reach_the_backend_and_do_not_collide_with_pages():
    nginx = (ROOT / "deploy/aws/nginx.conf").read_text(encoding="utf-8")
    vite = (ROOT / "frontend/vite.config.ts").read_text(encoding="utf-8")
    netlify = (ROOT / "frontend/scripts/write-netlify-redirects.mjs").read_text(encoding="utf-8")
    routes = (ROOT / "frontend/src/routes.tsx").read_text(encoding="utf-8")
    for prefix in ("support", "help-content"):
        assert f"|{prefix}|" in nginx and f"'{prefix}'" in vite and f"'{prefix}'" in netlify
        assert f"path: '{prefix}'" not in routes
