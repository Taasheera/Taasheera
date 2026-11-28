"""Group trips: one account, an application per traveler, trip answers asked once (CMPS271-81)."""

from sqlmodel import Session, select

from app.models import Application, ApplicationAnswer, GroupMember
from test_applications_api import ANSWERS, answer_all, api, put, start  # noqa: F401 (api is a fixture)


def ready(client, headers):
    application_id = start(client, headers)["id"]
    answer_all(client, headers, application_id)
    return application_id


def add(client, headers, application_id, label="Maria"):
    return client.post(f"/applications/{application_id}/group/members", headers=headers, json={"label": label})


def test_a_member_gets_the_trip_answers_but_not_the_owners_passport_or_job(api):
    client, app, headers, _ = api
    lead = ready(client, headers[0])
    response = add(client, headers[0], lead)
    assert response.status_code == 201
    group = response.json()
    assert [(m["label"], m["lead"]) for m in group["members"]] == [("Traveler", True), ("Maria", False)]
    member = group["members"][1]["application_id"]

    state = client.get(f"/applications/{member}", headers=headers[0]).json()
    answered = {answer["question"]["id"]: answer for answer in state["answers"]}
    for shared in ("main_destination", "purpose", "arrival_date", "departure_date", "accommodation", "host_details"):
        assert answered[shared]["value"] == ANSWERS[shared] and answered[shared]["source"] == "group"
    assert "passport" not in answered and "occupation" not in answered and "funds_amount" not in answered
    # The member is asked for their own passport, with no suggestion from the account owner's.
    assert state["status"] == "DRAFT"
    with Session(app.state.engine) as session:
        assert session.get(Application, lead).group_size == 2


def test_member_passport_questions_never_suggest_the_owners_details(api, monkeypatch):
    client, _, headers, passports = api
    lead = ready(client, headers[0])
    member = add(client, headers[0], lead).json()["members"][1]["application_id"]
    state = client.get(f"/applications/{member}", headers=headers[0]).json()
    while state["current"] and state["current"]["id"] != "passport":
        state = put(client, headers[0], member, state["current"]["id"], ANSWERS[state["current"]["id"]]).json()
    assert state["current"]["id"] == "passport" and state["current"]["suggestion"] is None


def test_copying_again_brings_members_up_to_date(api):
    client, _, headers, _ = api
    lead = ready(client, headers[0])
    member = add(client, headers[0], lead).json()["members"][1]["application_id"]
    assert put(client, headers[0], lead, "host_details", "Lina Example, 9 Rue Exemple, Paris").status_code == 200
    synced = client.post(f"/applications/{member}/group/sync", headers=headers[0])
    assert synced.status_code == 200
    state = client.get(f"/applications/{member}", headers=headers[0]).json()
    host = next(answer for answer in state["answers"] if answer["question"]["id"] == "host_details")
    assert host["value"] == "Lina Example, 9 Rue Exemple, Paris"


def test_groups_are_owner_only_and_validated(api):
    client, _, headers, _ = api
    lead = ready(client, headers[0])
    assert add(client, headers[1], lead).status_code == 404
    assert client.get(f"/applications/{lead}/group", headers=headers[1]).status_code == 404
    assert add(client, headers[0], lead, "   ").status_code == 422
    assert client.post(f"/applications/{lead}/group/members", headers=headers[0],
                       json={"label": "A", "extra": 1}).status_code == 422
    alone = client.get(f"/applications/{lead}/group", headers=headers[0]).json()
    assert alone["group_id"] is None and alone["can_add"] is True and "arrival_date" in alone["shared"]


def test_deleting_the_lead_leaves_the_members_standing_alone(api):
    client, app, headers, _ = api
    lead = ready(client, headers[0])
    member = add(client, headers[0], lead).json()["members"][1]["application_id"]
    assert client.delete(f"/applications/{lead}", headers=headers[0]).status_code == 204
    with Session(app.state.engine) as session:
        assert session.exec(select(GroupMember)).all() == []
        assert session.get(Application, member) is not None
        assert session.exec(select(ApplicationAnswer).where(ApplicationAnswer.application_id == member)).first()
    assert client.get(f"/applications/{member}/group", headers=headers[0]).json()["group_id"] is None


def test_deleting_a_member_shrinks_the_group(api):
    client, app, headers, _ = api
    lead = ready(client, headers[0])
    group = add(client, headers[0], lead).json()
    group = add(client, headers[0], lead, "Omar").json()
    assert len(group["members"]) == 3
    member = group["members"][1]["application_id"]
    assert client.delete(f"/applications/{member}", headers=headers[0]).status_code == 204
    assert len(client.get(f"/applications/{lead}/group", headers=headers[0]).json()["members"]) == 2
    with Session(app.state.engine) as session:
        assert session.get(Application, lead).group_size == 2



def test_every_member_knows_the_group_size(api):
    client, app, headers, _ = api
    lead = ready(client, headers[0])
    add(client, headers[0], lead)
    group = add(client, headers[0], lead, "Omar").json()
    with Session(app.state.engine) as session:
        assert {session.get(Application, m["application_id"]).group_size for m in group["members"]} == {3}
