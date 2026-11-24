"""The agent's queue, worker and API end to end (CMPS271-236, CMPS271-67, CMPS271-70).

The worker tests fill the bundled copy of the Schengen form in a real headless Chromium and
skip when it isn't installed.
"""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import Session, select

from app.agent import queue
from app.agent.storage import AgentFiles
from app.agent.worker import run_once
from app.models import AgentFieldEntry, AgentRun, AgentScreenshot, DocumentAccess, Notification
from test_applications_api import answer_all, api, start  # noqa: F401 (api is a fixture)


@pytest.fixture(scope="module")
def browser():
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except Exception as error:  # noqa: BLE001
            pytest.skip(f"Chromium for Playwright isn't installed: {type(error).__name__}")
        yield browser
        browser.close()


def ready_application(client, headers):
    application_id = start(client, headers)["id"]
    answer_all(client, headers, application_id)
    return application_id


def agent(client, headers, application_id, path="", method="get"):
    return getattr(client, method)(f"/applications/{application_id}/agent{path}", headers=headers)


def work(app, tmp_path, browser):
    @contextmanager
    def shared():
        yield browser

    return run_once(app.state.engine, AgentFiles(tmp_path / "passports"), shared)


def test_a_browser_that_will_not_start_fails_the_run(api, tmp_path):
    client, app, headers, _ = api
    application_id = ready_application(client, headers[0])
    agent(client, headers[0], application_id, "/runs", "post")

    @contextmanager
    def broken():
        raise RuntimeError("no browser")
        yield

    assert run_once(app.state.engine, AgentFiles(tmp_path / "passports"), broken) is True
    [run] = agent(client, headers[0], application_id).json()["runs"]
    assert run["status"] == "failed" and "browser didn't start" in run["message"]


def test_the_agent_starts_only_on_a_complete_application(api):
    client, _, headers, _ = api
    draft = start(client, headers[0])["id"]
    state = agent(client, headers[0], draft).json()
    assert state["site"] == {"id": "schengen-harmonised", "name": "Harmonised Schengen visa application form", "is_demo": True}
    assert state["can_start"] is False and "Answer every question" in state["reason"]
    assert agent(client, headers[0], draft, "/runs", "post").status_code == 409

    application_id = ready_application(client, headers[0])
    assert agent(client, headers[0], application_id).json()["can_start"] is True
    response = agent(client, headers[0], application_id, "/runs", "post")
    assert response.status_code == 201 and response.json()["status"] == "queued"
    # One run at a time per application.
    again = agent(client, headers[0], application_id, "/runs", "post")
    assert again.status_code == 409 and "already filling" in again.json()["detail"]
    # Another traveler can't see or start it.
    assert agent(client, headers[1], application_id).status_code == 404
    assert agent(client, headers[1], application_id, "/runs", "post").status_code == 404


def test_other_destinations_have_no_form_yet(api):
    client, _, headers, _ = api
    application_id = client.post("/applications", headers=headers[0],
                                 json={"destination": "GBR", "visa_type": "standard_visitor"}).json()["id"]
    state = agent(client, headers[0], application_id).json()
    assert state["site"] is None and "can't fill this destination" in state["reason"]


def test_the_routes_are_hidden_while_the_flag_is_off(api, monkeypatch):
    client, app, headers, _ = api
    application_id = start(client, headers[0])["id"]
    monkeypatch.setattr(app.state, "feature_flags", type(app.state.feature_flags)(frozenset()))
    assert agent(client, headers[0], application_id).status_code == 404


def test_the_worker_fills_the_form_records_everything_and_notifies(api, tmp_path, browser):
    client, app, headers, _ = api
    application_id = ready_application(client, headers[0])
    run_id = agent(client, headers[0], application_id, "/runs", "post").json()["id"]

    assert work(app, tmp_path, browser) is True
    assert work(app, tmp_path, browser) is False  # nothing left to do

    detail = agent(client, headers[0], application_id, f"/runs/{run_id}").json()
    assert detail["status"] == "ready" and "review and submit" in detail["message"]
    typed = {entry["field"]: entry for entry in detail["entries"]}
    assert typed["1. Surname"]["value"] == "ERIKSSON" and typed["1. Surname"]["page_title"] == "Personal details"
    assert typed["14. Number of travel document"]["value"] == "L898902C3"
    assert typed["19. Email address"]["value"] == "traveler1@example.com"
    assert [shot["page"] for shot in detail["screenshots"]] == [1, 2, 3, 4, 5]

    shot = detail["screenshots"][0]
    image = agent(client, headers[0], application_id, f"/runs/{run_id}/screenshots/{shot['id']}")
    assert image.status_code == 200 and image.content.startswith(b"\x89PNG")
    assert image.headers["cache-control"] == "private, no-store"
    assert agent(client, headers[1], application_id, f"/runs/{run_id}/screenshots/{shot['id']}").status_code == 404

    with Session(app.state.engine) as session:
        [notice] = session.exec(select(Notification).where(Notification.kind.like("agent_%"))).all()
        assert notice.kind == "agent_finished" and notice.traveler_id == 1
        assert "ERIKSSON" not in notice.message and "stopped before submitting" in notice.message
        audit = session.exec(select(DocumentAccess).where(DocumentAccess.purpose == "agent_screenshot")).all()
        assert [row.upload_id for row in audit] == [shot["id"]]
    assert (tmp_path / "passports" / "1" / "agent" / application_id / f"{shot['id']}.png").is_file()
    assert agent(client, headers[0], application_id).json()["can_start"] is True


def test_a_run_whose_answers_changed_stops_without_opening_the_site(api, tmp_path, browser):
    client, app, headers, _ = api
    application_id = ready_application(client, headers[0])
    run_id = agent(client, headers[0], application_id, "/runs", "post").json()["id"]
    client.put(f"/applications/{application_id}/answers/accommodation", headers=headers[0], json={"value": "hotel"})
    work(app, tmp_path, browser)
    detail = agent(client, headers[0], application_id, f"/runs/{run_id}").json()
    assert detail["status"] == "stopped" and "aren't complete" in detail["message"] and detail["entries"] == []
    with Session(app.state.engine) as session:
        assert session.exec(select(Notification.kind).where(Notification.kind == "agent_stopped")).all()


def test_a_cancelled_run_is_left_alone(api, tmp_path, browser):
    client, app, headers, _ = api
    application_id = ready_application(client, headers[0])
    run_id = agent(client, headers[0], application_id, "/runs", "post").json()["id"]
    response = agent(client, headers[0], application_id, f"/runs/{run_id}/cancel", "post")
    assert response.status_code == 200 and response.json()["status"] == "cancelled"
    assert work(app, tmp_path, browser) is False


def test_cancelling_while_running_stops_at_the_next_field(api):
    client, app, headers, _ = api
    application_id = ready_application(client, headers[0])
    agent(client, headers[0], application_id, "/runs", "post")
    with Session(app.state.engine) as session:
        run = queue.claim(session)
        queue.record_entry(session, run.id, page=1, page_title="Personal details", field="1. Surname", value="X")
        queue.cancel(session, run)
        with pytest.raises(queue.RunCancelled):
            queue.record_entry(session, run.id, page=1, page_title="Personal details", field="3. First name", value="Y")
        assert queue.finish(session, run.id, "ready", "done").status == "cancelled"


def test_a_run_is_claimed_once_and_dead_workers_are_recovered(api):
    client, app, headers, _ = api
    application_id = ready_application(client, headers[0])
    agent(client, headers[0], application_id, "/runs", "post")
    with Session(app.state.engine) as first, Session(app.state.engine) as second:
        assert queue.claim(first) is not None
        assert queue.claim(second) is None
        run = first.exec(select(AgentRun)).one()
        run.started_at = datetime.now(timezone.utc) - timedelta(hours=1)
        first.add(run)
        first.commit()
        assert queue.recover_stale(second) == 1
    assert agent(client, headers[0], application_id).json()["runs"][0]["status"] == "failed"


def test_deleting_the_application_deletes_its_runs_and_screenshots(api, tmp_path, browser):
    client, app, headers, _ = api
    application_id = ready_application(client, headers[0])
    agent(client, headers[0], application_id, "/runs", "post")
    work(app, tmp_path, browser)
    folder = tmp_path / "passports" / "1" / "agent" / application_id
    assert any(folder.iterdir())
    assert client.delete(f"/applications/{application_id}", headers=headers[0]).status_code == 204
    assert not folder.exists()
    with Session(app.state.engine) as session:
        for model in (AgentRun, AgentFieldEntry, AgentScreenshot):
            assert session.exec(select(model)).all() == []


def test_values_are_never_logged(api, tmp_path, browser, caplog):
    client, app, headers, _ = api
    application_id = ready_application(client, headers[0])
    agent(client, headers[0], application_id, "/runs", "post")
    with caplog.at_level("DEBUG"):
        work(app, tmp_path, browser)
    assert "ERIKSSON" not in caplog.text and "L898902C3" not in caplog.text


def test_the_worker_loop_keeps_its_heartbeat_fresh(api, tmp_path, monkeypatch):
    """The container's health check reads this file (deploy/aws/compose.yaml)."""
    from app.agent import worker

    _, app, _, _ = api
    heartbeat = tmp_path / "alive"
    monkeypatch.setattr(worker, "HEARTBEAT", heartbeat)
    monkeypatch.setattr(worker, "POLL_SECONDS", 0)
    loops = iter([False, False, True])
    worker.serve(app.state.engine, AgentFiles(tmp_path / "passports"), None, stop=lambda: next(loops))
    assert heartbeat.is_file()
