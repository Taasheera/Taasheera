"""Group trips: one account applies for several travelers at once (CMPS271-81).

Each traveler gets an ordinary application (their own passport, personal answers, documents and checks), linked
by a `GroupMember` row to the first, "lead" application. The trip answers everyone shares (where, when, where you
stay, what for) are asked once, on the lead, and copied to each member: when a member is added, and again when the
account owner presses "Copy trip answers to everyone". Copies keep the source "group", so the answers list says where
they came from; a member can still change their own copy. Copied answers are re-checked by the engine on the next
read, like any answer. A member's passport and profile questions never suggest the account owner's own details.
"""
from datetime import datetime, timezone
from typing import Annotated
import uuid

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlmodel import Session, delete, select

from ..models import Application, ApplicationAnswer, GroupMember, Traveler
from . import flow

MAX_GROUP = 10
# Trip answers that are the same for everyone travelling together. Anything about one person (passport,
# job, past travel and refusals, money) stays theirs.
SHARED = ("main_destination", "purpose", "stay_over_90_days", "stay_over_6_months", "work_or_study", "work_in_uk",
          "arrival_date", "departure_date", "itinerary_cities", "places_to_visit", "entries", "entry_city",
          "port_of_entry", "port_of_arrival", "airline", "accommodation", "accommodation_details", "host",
          "host_details", "canada_address", "uk_address", "us_address", "return_ticket")
SOURCE = "group"


class MemberRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1, max_length=60)

    @field_validator("label")
    @classmethod
    def trimmed(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("Give the traveler a name.")
        return value


class MemberPublic(BaseModel):
    application_id: str
    label: str
    lead: bool
    status: str
    answered: int
    total: int


class GroupPublic(BaseModel):
    group_id: str | None
    lead_id: str | None
    members: list[MemberPublic]
    shared: list[str]
    can_add: bool


def membership(session: Session, application_id: str) -> GroupMember | None:
    return session.get(GroupMember, application_id)


def is_member(session: Session, application_id: str) -> bool:
    """A non-lead traveler in a group: their questions must not suggest the account owner's passport or profile."""
    row = membership(session, application_id)
    return row is not None and not row.lead


def shared_ids(questionnaire: flow.Questionnaire) -> list[str]:
    asked = {question.id for question in questionnaire.questions()}
    return [question_id for question_id in SHARED if question_id in asked]


def copy_shared(session: Session, lead: Application, member: Application) -> int:
    """Copy the lead's shared trip answers onto a member's application. Returns how many changed."""
    questionnaire = flow.load_questionnaire(lead.questionnaire_id)
    lead_rows = {row.question_id: row for row in session.exec(
        select(ApplicationAnswer).where(ApplicationAnswer.application_id == lead.id))}
    member_rows = {row.question_id: row for row in session.exec(
        select(ApplicationAnswer).where(ApplicationAnswer.application_id == member.id))}
    changed = 0
    now = datetime.now(timezone.utc)
    for question_id in shared_ids(questionnaire):
        source = lead_rows.get(question_id)
        if source is None:
            continue
        row = member_rows.get(question_id)
        if row is not None and row.value == source.value:
            continue
        if row is None:
            row = ApplicationAnswer(application_id=member.id, question_id=question_id, value=source.value,
                                    source=SOURCE, answered_at=now)
        else:
            row.value, row.source, row.answered_at = source.value, SOURCE, now
        session.add(row)
        changed += 1
    if changed:
        member.updated_at = now
        session.add(member)
    return changed


def resize(session: Session, group_id: str) -> int:
    """Set `group_size` on every application in the group to the number of travelers in it."""
    rows = session.exec(select(GroupMember).where(GroupMember.group_id == group_id)).all()
    size = max(1, len(rows))
    for member in rows:
        application = session.get(Application, member.application_id)
        if application is not None and application.group_size != size:
            application.group_size = size
            session.add(application)
    return size


def delete_group_rows(session: Session, application: Application) -> None:
    """Before an application is deleted: drop its own row, and if it led a group, let the others stand alone."""
    row = membership(session, application.id)
    if row is None:
        return
    if row.lead:
        session.exec(delete(GroupMember).where(GroupMember.group_id == row.group_id))
    else:
        session.delete(row)
        session.flush()
        resize(session, row.group_id)


def add_group_routes(router: APIRouter, get_session, current_traveler, *, today) -> None:
    from ..auth import check_origin
    from ..features import require_feature
    from .router import _status, current_answers, is_recorded

    SessionDep = Annotated[Session, Depends(get_session)]
    TravelerDep = Annotated[Traveler, Depends(current_traveler)]
    flagged = [Depends(require_feature("group_applications"))]

    def owned(session: Session, traveler: Traveler, application_id: str) -> Application:
        application = session.get(Application, application_id)
        if application is None or application.traveler_id != traveler.id:
            raise HTTPException(status_code=404, detail="Application not found.")
        return application

    def public(session: Session, traveler: Traveler, application: Application) -> GroupPublic:
        row = membership(session, application.id)
        questionnaire = flow.load_questionnaire(application.questionnaire_id)
        if row is None:
            return GroupPublic(group_id=None, lead_id=None, members=[], shared=shared_ids(questionnaire),
                               can_add=application.archived_at is None and not is_recorded(application))
        rows = session.exec(select(GroupMember).where(GroupMember.group_id == row.group_id,
                                                      GroupMember.traveler_id == traveler.id)
                            .order_by(GroupMember.lead.desc(), GroupMember.created_at)).all()
        members = []
        for member in rows:
            other = session.get(Application, member.application_id)
            if other is None:
                continue
            stored = {r.question_id: r.value for r in session.exec(
                select(ApplicationAnswer).where(ApplicationAnswer.application_id == other.id))}
            answers, _ = current_answers(questionnaire, other, stored, today())
            progress = flow.progress(questionnaire, answers)
            members.append(MemberPublic(application_id=other.id, label=member.label, lead=member.lead,
                                        status=other.status if is_recorded(other) else _status(questionnaire, answers),
                                        answered=progress.answered, total=progress.total))
        lead = session.get(Application, row.group_id)
        return GroupPublic(group_id=row.group_id, lead_id=row.group_id, members=members, shared=shared_ids(questionnaire),
                           can_add=bool(lead and lead.archived_at is None and not is_recorded(lead)
                                        and len(members) < MAX_GROUP))

    @router.get("/{application_id}/group", response_model=GroupPublic, dependencies=flagged)
    def group(application_id: str, traveler: TravelerDep, session: SessionDep, response: Response):
        response.headers["Cache-Control"] = "private, no-store"
        return public(session, traveler, owned(session, traveler, application_id))

    @router.post("/{application_id}/group/members", response_model=GroupPublic, status_code=201,
                 dependencies=[Depends(check_origin), *flagged])
    def add_member(application_id: str, data: MemberRequest, traveler: TravelerDep, session: SessionDep,
                   response: Response):
        """Start an application for another traveler on the same trip, with the trip answers filled in."""
        response.headers["Cache-Control"] = "private, no-store"
        application = owned(session, traveler, application_id)
        row = membership(session, application.id)
        lead = session.get(Application, row.group_id) if row else application
        if lead is None or lead.archived_at is not None or is_recorded(lead):
            raise HTTPException(status_code=409, detail="Restore or reopen the trip's first application before adding travelers.")
        count = len(session.exec(select(GroupMember.application_id).where(GroupMember.group_id == lead.id)).all())
        if count >= MAX_GROUP:
            raise HTTPException(status_code=409, detail=f"A group can have up to {MAX_GROUP} travelers.")
        if row is None:
            session.add(GroupMember(application_id=lead.id, traveler_id=traveler.id, group_id=lead.id,
                                    label=traveler.name.split()[0] if traveler.name.strip() else "You", lead=True))
            count = 1
        member = Application(id=uuid.uuid4().hex, traveler_id=traveler.id, questionnaire_id=lead.questionnaire_id,
                             questionnaire_version=lead.questionnaire_version, destination=lead.destination,
                             visa_type=lead.visa_type, group_size=1)
        session.add(member)
        session.flush()
        session.add(GroupMember(application_id=member.id, traveler_id=traveler.id, group_id=lead.id, label=data.label))
        copy_shared(session, lead, member)
        session.flush()
        resize(session, lead.id)
        session.commit()
        return public(session, traveler, lead)

    @router.post("/{application_id}/group/sync", response_model=GroupPublic, dependencies=[Depends(check_origin), *flagged])
    def sync(application_id: str, traveler: TravelerDep, session: SessionDep, response: Response):
        """Copy the lead's current trip answers to every member still being prepared."""
        response.headers["Cache-Control"] = "private, no-store"
        application = owned(session, traveler, application_id)
        row = membership(session, application.id)
        if row is None:
            raise HTTPException(status_code=404, detail="This application isn't part of a group.")
        lead = session.get(Application, row.group_id)
        if lead is None:
            raise HTTPException(status_code=404, detail="This application isn't part of a group.")
        for member in session.exec(select(GroupMember).where(GroupMember.group_id == lead.id,
                                                             GroupMember.lead.is_(False))).all():
            other = session.get(Application, member.application_id)
            if other is not None and other.archived_at is None and not is_recorded(other):
                copy_shared(session, lead, other)
        session.commit()
        return public(session, traveler, lead)
