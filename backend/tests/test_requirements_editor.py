"""Destination requirements written in the back-office without a deploy (CMPS271-123)."""

import copy
import json
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.routing import iter_route_contexts
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlmodel import Session, select

from app.applications import destination_check, flow, form_pdf, portal_guide, visa_guides
from app.auth import access_token
from app.main import create_app
from app.models import ActivityEvent, Application, AuthSession, RequirementRevision, Traveler
from app.privacy import erase_account
from app.requirements import RequirementStore
from test_application_flow import TODAY, _generic_answer
from test_applications_api import ANSWERS, START, answer_all

SCHENGEN = "schengen-short-stay"
BASE = "/admin/requirements"
NEW_QUESTION = {"id": "insurance_bought", "type": "yes_no", "prompt": "Have you bought travel insurance yet?"}


@pytest.fixture
def api(tmp_path):
    app = create_app(f"sqlite:///{(tmp_path / 'requirements.db').as_posix()}")
    with TestClient(app) as client:
        headers, ids = {}, {}
        with Session(app.state.engine) as session:
            for number, role in enumerate(("admin", "support", "traveler", "admin2"), start=1):
                traveler = Traveler(name=f"{role.title()} Example", email=f"{role}@example.com", password_hash="test-hash",
                                    role="admin" if role == "admin2" else role)
                session.add(traveler)
                session.flush()
                auth = AuthSession(id=f"requirements-session-{number}", traveler_id=traveler.id,
                                   refresh_hash=f"{number}" * 64, expires_at=2**31)
                session.add(auth)
                headers[role] = {"Authorization": f"Bearer {access_token(auth, app.state.auth_settings).access_token}"}
                ids[role] = traveler.id
            session.commit()
        yield SimpleNamespace(client=client, app=app, headers=headers, ids=ids)


def packaged_content(questionnaire_id=SCHENGEN):
    guide, checked_on = destination_check.packaged_guide(questionnaire_id)
    return destination_check.packaged_file(questionnaire_id), destination_check.guide_json(guide, checked_on)


def questions(data):
    return [question for step in data["steps"] for question in step["questions"]]


def question(data, question_id):
    return next(item for item in questions(data) if item["id"] == question_id)


def check(questionnaire, guide, questionnaire_id=SCHENGEN):
    return destination_check.check(questionnaire_id, questionnaire, guide, version=1, today=date.today()).problems


def edit(api, change, questionnaire_id=SCHENGEN, who="admin"):
    """Save a draft of the current version with `change(questionnaire, guide)` applied."""
    current = api.client.get(f"{BASE}/{questionnaire_id}", headers=api.headers[who]).json()
    questionnaire, guide = copy.deepcopy(current["current"]["questionnaire"]), copy.deepcopy(current["current"]["guide"])
    change(questionnaire, guide)
    response = api.client.put(f"{BASE}/{questionnaire_id}/draft", headers=api.headers[who],
                              json={"questionnaire": questionnaire, "guide": guide, "base_revision": current["revision"]})
    assert response.status_code == 200, response.text
    return response.json(), current["revision"]


def publish(api, change, note="Synthetic change", questionnaire_id=SCHENGEN, who="admin"):
    saved, revision = edit(api, change, questionnaire_id, who)
    response = api.client.post(f"{BASE}/{questionnaire_id}/publish", headers=api.headers[who],
                               json={"base_revision": revision, "note": note})
    assert response.status_code == 200, response.text
    return response.json()


def rollback(api, revision, note="Back", questionnaire_id=SCHENGEN):
    current = api.client.get(f"{BASE}/{questionnaire_id}", headers=api.headers["admin"]).json()
    return api.client.post(f"{BASE}/{questionnaire_id}/rollback", headers=api.headers["admin"],
                           json={"revision": revision, "base_revision": current["revision"], "note": note})


def add_question_and_drop_single_entry(questionnaire, guide):
    trip = next(step for step in questionnaire["steps"] if step["id"] == "trip")
    trip["questions"].append(dict(NEW_QUESTION))
    entries = question(questionnaire, "entries")
    entries["options"] = [option for option in entries["options"] if option["value"] != "single"]


def stored_version(api, application_id):
    with Session(api.app.state.engine) as session:
        return session.get(Application, application_id).questionnaire_version


@pytest.mark.parametrize("questionnaire_id", [q.id for q in flow.available_questionnaires()])
def test_every_packaged_destination_passes_the_editor_checks(questionnaire_id):
    assert check(*packaged_content(questionnaire_id), questionnaire_id) == []


def _set(path, value):
    def change(data):
        node = data
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value
    return change


def _drop(path):
    def change(data):
        node = data
        for key in path[:-1]:
            node = node[key]
        del node[path[-1]]
    return change


QUESTIONNAIRE_MISTAKES = [
    (_set(["steps", 0, "questions", 1, "id"], "main_destination"), [], "used twice"),
    (_set(["steps", 2, "questions", 7, "show_iff"], {"question": "occupation", "in": ["employed"]}),
     ["steps", 2, "questions", 7, "show_iff"], "Extra inputs are not permitted"),
    (_set(["steps", 2, "questions", 7, "show_if", "question"], "arrival_date"), [], "earlier question"),
    (_drop(["steps", 2, "questions", 3, "options"]), [], "need options"),
    (_set(["steps", 2, "questions", 1, "type"], "number"), ["steps", 2, "questions", 1, "type"], "Input should be"),
    (_set(["steps", 2, "questions", 1, "prefill"], "passport"), [], "doesn't give a text answer"),
    (_set(["steps", 3, "questions", 3, "options", 0, "value"], ""), ["steps", 3, "questions", 3, "options", 0, "value"],
     "at least 1 character"),
    (_set(["documents", 3, "show_if"], {"question": "accommodation", "in": ["castle"]}), [], "can't have"),
    (_set(["steps"], []), ["steps"], "at least 1 item"),
]


@pytest.mark.parametrize("change, loc, words", QUESTIONNAIRE_MISTAKES)
def test_the_editor_refuses_what_the_questionnaire_loader_refuses(change, loc, words):
    questionnaire, guide = packaged_content()
    change(questionnaire)
    with pytest.raises(ValidationError, match=words):
        flow.Questionnaire.model_validate_json(json.dumps(questionnaire))
    problems = check(questionnaire, guide)
    assert any(problem.part == "questionnaire" and problem.loc == loc and words in problem.message
               for problem in problems), problems


def test_problems_name_the_place_by_its_ids():
    questionnaire, guide = packaged_content()
    question(questionnaire, "job_title")["show_iff"] = {"question": "occupation", "in": ["employed"]}
    [problem] = check(questionnaire, guide)
    assert problem.path == "steps[personal].questions[job_title].show_iff"


def _data_file_walk_fails(questionnaire):
    """The walk tests/test_application_flow.py runs on every packaged file."""
    given = {}
    try:
        while (asked := flow.next_question(questionnaire, given)) is not None:
            given[asked.id] = flow.check_answer(asked, _generic_answer(asked, TODAY), given, TODAY)
    except flow.InvalidAnswer:
        return True
    return bool(flow.stop_message(questionnaire, given)) or not flow.required_documents(questionnaire, given) or not any(
        q.type == "passport" and q.prefill == "passport" for q in questionnaire.questions())


def _stop_every_purpose(data):
    purpose = question(data, "purpose")
    purpose["stop_if"] = {"in": [option["value"] for option in purpose["options"]], "message": "Not this visa."}


@pytest.mark.parametrize("change, words", [
    (_stop_every_purpose, "stop the questionnaire before the end: Not this visa."),
    (lambda data: question(data, "passport").pop("prefill"), '"prefill": "passport"'),
    (lambda data: data.update(documents=[]), "supporting document"),
])
def test_the_editor_refuses_a_questionnaire_travelers_cannot_finish(change, words):
    questionnaire, guide = packaged_content()
    change(questionnaire)
    assert _data_file_walk_fails(flow.Questionnaire.model_validate_json(json.dumps(questionnaire)))
    assert any(words in problem.message for problem in check(questionnaire, guide))


def _remove_question(data, question_id):
    for step in data["steps"]:
        step["questions"] = [item for item in step["questions"] if item["id"] != question_id]


def _referenced(data, question_id):
    text = json.dumps({**data, "steps": [{**step, "questions": [q for q in step["questions"] if q["id"] != question_id]}
                                         for step in data["steps"]]})
    return f'"{question_id}"' in text or f'"{question_id}.' in text


def test_the_official_form_guide_keeps_its_questions():
    questionnaire, guide = packaged_content("uk-standard-visitor")
    form_guide = portal_guide.read_guide("uk-standard-visitor")
    used = {source.split(".")[0] for section in form_guide.sections for field in section.fields for source in field.sources}
    removed = next(question_id for question_id in sorted(used - {"account_email", "passport"})
                   if not _referenced(questionnaire, question_id))
    _remove_question(questionnaire, removed)
    with pytest.raises(ValueError, match=f"Unknown question {removed}"):
        portal_guide.check_guide(form_guide, flow.Questionnaire.model_validate_json(json.dumps(questionnaire)))
    assert any("portals/uk-standard-visitor.json" in problem.message and removed in problem.message
               for problem in check(questionnaire, guide, "uk-standard-visitor"))


def test_the_schengen_pdf_map_keeps_its_questions():
    questionnaire, guide = packaged_content()
    form, pdf = form_pdf.read_form(SCHENGEN)
    used = {source.split(".")[0] for field in form.fields for source in field.sources}
    removed = next(question_id for question_id in sorted(used - {"account_email", "passport"})
                   if not _referenced(questionnaire, question_id))
    _remove_question(questionnaire, removed)
    with pytest.raises(ValueError, match="embassy PDF field map"):
        form_pdf.check_form(form, pdf, flow.Questionnaire.model_validate_json(json.dumps(questionnaire)))
    assert any("forms/schengen-short-stay.json" in problem.message for problem in check(questionnaire, guide))


GUIDE_MISTAKES = [
    (["fees", "source", "url"], "https://visa-help.example.com/fees", "allowed official site"),
    (["fees", "items", 0, "amount"], "x" * 21, "at most 20 characters"),
    (["time", "text"], "", "at least 1 character"),
    (["coverage", "level"], "magic", "Input should be"),
    (["fees", "discount"], "10%", "Extra inputs are not permitted"),
    (["checked_on"], "25/11/2026", "String should match pattern"),
]


@pytest.mark.parametrize("loc, value, words", GUIDE_MISTAKES)
def test_the_editor_refuses_what_the_visa_guide_loader_refuses(loc, value, words):
    questionnaire, guide = packaged_content()
    _set(loc, value)(guide)
    data = json.loads(visa_guides.GUIDES_FILE.read_text(encoding="utf-8"))
    entry = next(item for item in data["guides"] if item["questionnaire_id"] == SCHENGEN)
    entry.update({key: item for key, item in guide.items() if key != "checked_on"})
    data["checked_on"] = guide["checked_on"]
    with pytest.raises(ValidationError, match=words):
        visa_guides.GuidesFile.model_validate(data)
    problems = check(questionnaire, guide)
    assert [(problem.part, problem.loc) for problem in problems if words in problem.message] == [("guide", loc)], problems


def test_figures_cannot_be_checked_in_the_future_or_move_the_visa():
    questionnaire, guide = packaged_content()
    guide["checked_on"] = (date.today() + timedelta(days=1)).isoformat()
    questionnaire["destination"] = "GBR"
    problems = {tuple(problem.loc): problem.message for problem in check(questionnaire, guide)}
    assert "future" in problems[("checked_on",)] and "SCHENGEN" in problems[("destination",)]


def test_a_structural_publish_bumps_the_version_and_applications_reconcile(api):
    traveler = api.headers["traveler"]
    application = api.client.post("/applications", headers=traveler, json=START).json()["id"]
    assert answer_all(api.client, traveler, application)["status"] == "ANSWERS_COMPLETE"
    recorded = api.client.post("/applications", headers=traveler, json=START).json()["id"]
    answer_all(api.client, traveler, recorded)
    with Session(api.app.state.engine) as session:
        row = session.get(Application, recorded)
        row.status, row.submitted_at = "SUBMITTED", datetime.now(timezone.utc)
        session.add(row)
        session.commit()

    before = api.client.get(f"{BASE}/{SCHENGEN}", headers=api.headers["admin"]).json()
    assert (before["revision"], before["questionnaire_version"], before["applications_active"],
            before["applications_recorded"]) == (0, 1, 1, 1)
    saved, _ = edit(api, add_question_and_drop_single_entry)
    assert saved["check"]["valid"] and saved["check"]["questionnaire_version"] == 2
    paths = {(change["kind"], change["path"]) for change in saved["check"]["changes"]}
    assert {("removed", "steps[trip].questions[entries].options[single]"),
            ("added", "steps[trip].questions[insurance_bought]")} <= paths
    published = api.client.post(f"{BASE}/{SCHENGEN}/publish", headers=api.headers["admin"],
                                json={"base_revision": 0, "note": "Ask about insurance; single entry is gone"})
    assert published.status_code == 200
    assert (published.json()["revision"], published.json()["questionnaire_version"]) == (1, 2)
    assert published.json()["draft"] is None

    state = api.client.get(f"/applications/{application}", headers=traveler).json()
    assert state["status"] == "DRAFT" and state["cleared"] == ["entries"] and state["current"]["id"] == "entries"
    assert stored_version(api, application) == 2
    assert api.client.get(f"/applications/{recorded}", headers=traveler).json()["status"] == "SUBMITTED"
    assert stored_version(api, recorded) == 1
    started = api.client.post("/applications", headers=traveler, json=START).json()["id"]
    assert stored_version(api, started) == 2

    state = answer_all(api.client, traveler, application, {**ANSWERS, "entries": "multiple", "insurance_bought": True})
    assert state["status"] == "ANSWERS_COMPLETE"


def test_wording_changes_keep_the_version(api):
    publish(api, add_question_and_drop_single_entry)
    detail = publish(api, lambda q, g: question(q, "insurance_bought").update(prompt="Is your travel insurance bought?"))
    assert (detail["revision"], detail["questionnaire_version"]) == (2, 2)
    assert flow.load_questionnaire(SCHENGEN).question("insurance_bought").prompt == "Is your travel insurance bought?"


def test_rollback_returns_to_any_earlier_version_with_a_higher_version_number(api):
    traveler = api.headers["traveler"]
    application = api.client.post("/applications", headers=traveler, json=START).json()["id"]
    answer_all(api.client, traveler, application)
    publish(api, add_question_and_drop_single_entry, note="First")
    assert api.client.get(f"/applications/{application}", headers=traveler).json()["cleared"] == ["entries"]
    publish(api, lambda q, g: g["fees"]["items"][0].update(amount="€95"), note="Fee")

    back = rollback(api, 0, note="Back to the packaged files")
    assert back.status_code == 200, back.text
    assert (back.json()["revision"], back.json()["questionnaire_version"]) == (3, 3)
    assert flow.load_questionnaire(SCHENGEN).question("insurance_bought") is None
    assert flow.load_questionnaire(SCHENGEN).version == 3
    state = api.client.get(f"/applications/{application}", headers=traveler).json()
    assert state["current"]["id"] == "entries" and stored_version(api, application) == 3

    again = rollback(api, 2, note="The fee was right")
    assert (again.json()["revision"], again.json()["questionnaire_version"]) == (4, 4)
    assert flow.load_questionnaire(SCHENGEN).question("insurance_bought") is not None
    history = again.json()["history"]
    assert [(item["revision"], item["kind"], item["restored_revision"], item["questionnaire_version"], item["note"],
             item["author"]) for item in history] == [
        (4, "rollback", 2, 4, "The fee was right", "Admin Example"),
        (3, "rollback", 0, 3, "Back to the packaged files", "Admin Example"),
        (2, "publish", None, 2, "Fee", "Admin Example"),
        (1, "publish", None, 2, "First", "Admin Example")]
    old = api.client.get(f"{BASE}/{SCHENGEN}/revisions/0", headers=api.headers["admin"]).json()
    assert old == dict(zip(("questionnaire", "guide"), packaged_content()))

    with Session(api.app.state.engine) as session:
        audit = session.exec(select(ActivityEvent.event_type, ActivityEvent.identifier, ActivityEvent.outcome)
                             .where(ActivityEvent.traveler_id == api.ids["admin"])
                             .where(ActivityEvent.event_type.startswith("requirements_"))
                             .order_by(ActivityEvent.id)).all()
    assert audit == [("requirements_publish", f"{SCHENGEN}:r1", "success"),
                     ("requirements_publish", f"{SCHENGEN}:r2", "success"),
                     ("requirements_rollback", f"{SCHENGEN}:r3", "success"),
                     ("requirements_rollback", f"{SCHENGEN}:r4", "success")]


def test_published_guide_facts_reach_the_public_pages(api):
    publish(api, lambda q, g: (g["fees"]["items"][0].update(amount="€95"), g.update(checked_on=date.today().isoformat())))
    guide = api.client.get(f"/visa-guides/{SCHENGEN}").json()
    assert guide["fees"]["items"][0]["amount"] == "€95" and guide["checked_on"] == date.today().isoformat()
    others = {item["id"]: item for item in api.client.get("/visa-guides").json()}
    assert others["uk-standard-visitor"]["checked_on"] == "2026-11-25"
    assert rollback(api, 0).status_code == 200
    assert api.client.get(f"/visa-guides/{SCHENGEN}").json()["fees"]["items"][0]["amount"] == "€90"


def test_invalid_stale_or_empty_changes_are_refused(api):
    admin = api.headers["admin"]
    assert api.client.post(f"{BASE}/{SCHENGEN}/publish", headers=admin,
                           json={"base_revision": 0, "note": "No draft"}).status_code == 409
    saved, _ = edit(api, lambda q, g: g["fees"]["source"].update(url="http://example.com/fees"))
    assert saved["check"]["valid"] is False
    assert [(p["part"], p["loc"]) for p in saved["check"]["problems"]] == [("guide", ["fees", "source", "url"])]
    refused = api.client.post(f"{BASE}/{SCHENGEN}/publish", headers=admin, json={"base_revision": 0, "note": "Bad"})
    assert refused.status_code == 422
    assert refused.json()["detail"]["problems"][0]["path"] == "fees.source.url"

    edit(api, lambda q, g: None)
    assert api.client.post(f"{BASE}/{SCHENGEN}/publish", headers=admin,
                           json={"base_revision": 0, "note": "Same"}).status_code == 409
    publish(api, lambda q, g: g["fees"]["items"][0].update(amount="€95"), who="admin2")
    edit_stale = api.client.put(f"{BASE}/{SCHENGEN}/draft", headers=admin,
                                json={**dict(zip(("questionnaire", "guide"), packaged_content())), "base_revision": 0})
    assert edit_stale.status_code == 200
    stale = api.client.post(f"{BASE}/{SCHENGEN}/publish", headers=admin, json={"base_revision": 0, "note": "Old"})
    assert stale.status_code == 409 and "newer version" in stale.json()["detail"]
    assert rollback(api, 1).status_code == 409
    assert rollback(api, 9).status_code == 404
    assert api.client.get(f"{BASE}/mars-tourist", headers=admin).status_code == 404
    assert api.client.post(f"{BASE}/{SCHENGEN}/publish", headers=admin,
                           json={"base_revision": 1, "note": "  "}).status_code == 422
    assert api.client.put(f"{BASE}/{SCHENGEN}/draft", headers=admin,
                          json={"questionnaire": {}, "guide": {}, "base_revision": 0, "extra": 1}).status_code == 422


def test_drafts_are_saved_shown_and_discarded(api):
    admin = api.headers["admin"]
    saved, _ = edit(api, lambda q, g: g["time"].update(text="About two weeks."))
    assert saved["draft"]["author"] == "Admin Example" and saved["check"]["valid"]
    detail = api.client.get(f"{BASE}/{SCHENGEN}", headers=admin).json()
    assert detail["has_draft"] and detail["draft"]["guide"]["time"]["text"] == "About two weeks."
    assert flow.load_questionnaire(SCHENGEN).version == 1
    assert api.client.delete(f"{BASE}/{SCHENGEN}/draft", headers=admin).status_code == 204
    assert api.client.get(f"{BASE}/{SCHENGEN}", headers=admin).json()["draft"] is None
    assert api.client.delete(f"{BASE}/{SCHENGEN}/draft", headers=admin).status_code == 404
    listed = {item["id"]: item for item in api.client.get(BASE, headers=admin).json()}
    assert set(listed) == {q.id for q in flow.available_questionnaires()}
    assert listed[SCHENGEN]["revision"] == 0 and listed[SCHENGEN]["has_draft"] is False


def test_the_publishing_process_sees_a_publish_at_once(api):
    assert flow.load_questionnaire(SCHENGEN).question("insurance_bought") is None
    publish(api, add_question_and_drop_single_entry)
    assert flow.load_questionnaire(SCHENGEN).question("insurance_bought") is not None
    assert [q.id for q in flow.available_questionnaires()].count(SCHENGEN) == 1


def test_other_processes_pick_up_a_publish_within_the_interval(api):
    now = [100.0]
    other = RequirementStore(api.app.state.engine, interval=2, clock=lambda: now[0])
    other.install()
    try:
        questionnaire, guide = packaged_content()
        add_question_and_drop_single_entry(questionnaire, guide)
        guide["fees"]["items"][0]["amount"] = "€99"
        questionnaire["version"] = 2
        with Session(api.app.state.engine) as session:
            session.add(RequirementRevision(questionnaire_id=SCHENGEN, revision=1, kind="publish", questionnaire=questionnaire,
                                            guide=guide, questionnaire_version=2, note="Elsewhere"))
            session.commit()
        now[0] += 1
        assert flow.load_questionnaire(SCHENGEN).version == 1
        assert next(g for g in visa_guides.current_guides() if g.id == SCHENGEN).fees.items[0].amount == "€90"
        now[0] += 2
        assert flow.load_questionnaire(SCHENGEN).version == 2
        assert next(g for g in visa_guides.current_guides() if g.id == SCHENGEN).fees.items[0].amount == "€99"
    finally:
        other.uninstall()
    assert flow.PUBLISHED == {} and visa_guides.PUBLISHED == {}


def test_a_revision_that_no_longer_loads_falls_back_to_the_packaged_files(api, caplog):
    with Session(api.app.state.engine) as session:
        session.add(RequirementRevision(questionnaire_id=SCHENGEN, revision=1, kind="publish", questionnaire={"id": SCHENGEN},
                                        guide={}, questionnaire_version=2, note="Broken by a later deploy"))
        session.commit()
    store = RequirementStore(api.app.state.engine)
    store.install()
    try:
        assert flow.load_questionnaire(SCHENGEN) is flow.packaged_questionnaire(SCHENGEN)
    finally:
        store.uninstall()
    assert "no longer load" in caplog.text and "Broken" not in caplog.text
    assert api.client.get(f"{BASE}/{SCHENGEN}", headers=api.headers["admin"]).json()["questionnaire_version"] == 1


def _routes(app, prefix):
    return [(method, context.path) for context in iter_route_contexts(app.routes)
            if (context.path or "").startswith(prefix) for method in sorted(context.methods)]


@pytest.mark.parametrize("prefix", [BASE, "/admin/kpis"])
def test_only_admins_reach_the_editor_and_kpis(api, prefix):
    routes = _routes(api.app, prefix)
    assert routes
    for method, path in routes:
        path = path.replace("{questionnaire_id}", SCHENGEN).replace("{revision}", "0")
        assert api.client.request(method, path).status_code == 401
        for role in ("support", "traveler"):
            response = api.client.request(method, path, headers=api.headers[role])
            assert response.status_code == 403 and response.json() == {"detail": "Admin access required."}, (method, path)
            assert response.headers["cache-control"] == "no-store"
    assert api.client.get(prefix if prefix != BASE else f"{BASE}/{SCHENGEN}", headers=api.headers["admin"]).status_code == 200


def test_with_the_flag_off_the_editor_is_hidden(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATURE_REQUIREMENTS_EDITOR", "false")
    with TestClient(create_app(f"sqlite:///{(tmp_path / 'off.db').as_posix()}")) as client:
        assert client.get(BASE).status_code == 404
        assert client.get("/features").json()["requirements_editor"] is False


def test_erasing_the_author_keeps_what_they_published(api):
    publish(api, add_question_and_drop_single_entry, who="admin2")
    with Session(api.app.state.engine) as session:
        assert erase_account(session, session.get(Traveler, api.ids["admin2"]), api.app.state.passport_storage,
                             api.app.state.activity_directory, api.app.state.auth_settings.jwt_secret)
    with Session(api.app.state.engine) as session:
        [row] = session.exec(select(RequirementRevision)).all()
        assert row.author_id is None and row.questionnaire is not None
    detail = api.client.get(f"{BASE}/{SCHENGEN}", headers=api.headers["admin"]).json()
    assert detail["history"][0]["author"] is None and detail["questionnaire_version"] == 2
