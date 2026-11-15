"""Exporting an application record as a file (US 80, CMPS271-83)."""

from datetime import date
import json

from sqlmodel import Session, select

from app.applications.records import export_filename
from app.models import ActivityEvent, Application
from test_applications_api import ANSWERS, answer_all, api, start  # noqa: F401 (api is a fixture)


def export(client, headers, application_id):
    return client.get(f"/applications/{application_id}/export", headers=headers)


def test_the_file_holds_the_answers_status_and_timeline(api):
    client, app, headers, _ = api
    application_id = start(client, headers[0])["id"]
    answer_all(client, headers[0], application_id)
    client.post(f"/applications/{application_id}/decision", headers=headers[0], json={"record": "SUBMITTED"})

    response = export(client, headers[0], application_id)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["content-disposition"] == \
        f'attachment; filename="taasheera-france-{date.today().isoformat()}.json"'
    record = json.loads(response.content)
    assert record["format"] == "taasheera.application-record" and record["version"] == 1
    assert "not give legal or immigration advice" in record["notice"]
    assert record["application"]["id"] == application_id
    assert record["application"]["status"] == "SUBMITTED"
    assert record["application"]["submitted_at"] is not None
    assert record["questionnaire_id"] == "schengen-short-stay"

    answers = {answer["question_id"]: answer for answer in record["answers"]}
    assert set(answers) <= set(ANSWERS) and len(answers) >= 19
    assert answers["main_destination"]["value"] == "FRA" and answers["main_destination"]["label"] == "France"
    assert answers["stay_over_90_days"]["label"] == "No"
    assert answers["passport"]["value"]["document_number"] == ANSWERS["passport"]["document_number"]
    assert answers["passport"]["step"] and answers["passport"]["question"]
    assert all(answer["source"] and answer["answered_at"] for answer in record["answers"])

    timeline = client.get(f"/applications/{application_id}/timeline", headers=headers[0]).json()
    assert record["timeline"] == timeline


def test_each_download_is_audited_without_its_contents(api):
    client, app, headers, _ = api
    application_id = start(client, headers[0])["id"]
    answer_all(client, headers[0], application_id, stop_at="passport")
    export(client, headers[0], application_id)
    with Session(app.state.engine) as session:
        traveler_id = session.get(Application, application_id).traveler_id
        [event] = session.exec(select(ActivityEvent).where(ActivityEvent.event_type == "application_export")).all()
    assert (event.traveler_id, event.identifier, event.outcome) == (traveler_id, "application", "success")


def test_only_the_owner_can_export(api):
    client, app, headers, _ = api
    application_id = start(client, headers[0])["id"]
    assert export(client, headers[1], application_id).status_code == 404
    assert export(client, headers[0], "f" * 32).status_code == 404
    assert client.get(f"/applications/{application_id}/export").status_code == 401
    with Session(app.state.engine) as session:
        assert session.exec(select(ActivityEvent).where(ActivityEvent.event_type == "application_export")).all() == []


def test_a_new_or_archived_application_exports_too(api):
    client, _, headers, _ = api
    application_id = start(client, headers[0])["id"]
    record = json.loads(export(client, headers[0], application_id).content)
    assert record["answers"] == [] and record["application"]["status"] == "DRAFT"
    assert record["timeline"]["events"][0]["kind"] == "started"
    client.post(f"/applications/{application_id}/archive", headers=headers[0])
    record = json.loads(export(client, headers[0], application_id).content)
    assert record["application"]["archived_at"] is not None
    # No country chosen yet: named by the destination.
    assert "taasheera-schengen-area-" in export(client, headers[0], application_id).headers["content-disposition"]


def test_exporting_changes_nothing(api):
    client, app, headers, _ = api
    application_id = start(client, headers[0])["id"]
    answer_all(client, headers[0], application_id, stop_at="passport")
    before = client.get(f"/applications/{application_id}", headers=headers[0]).json()
    export(client, headers[0], application_id)
    assert client.get(f"/applications/{application_id}", headers=headers[0]).json() == before


def test_filenames_are_plain_ascii():
    class Summary:
        place, destination_label = None, "Schengen area"
    assert export_filename(Summary, date(2026, 11, 15)) == "taasheera-schengen-area-2026-11-15.json"
    Summary.place = 'Côte "d’Ivoire"/../x'
    assert export_filename(Summary, date(2026, 11, 15)) == "taasheera-c-te-d-ivoire-x-2026-11-15.json"
    Summary.place = "日本"
    assert export_filename(Summary, date(2026, 11, 15)) == "taasheera-application-2026-11-15.json"
