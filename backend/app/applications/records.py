"""Duplicate an application for another country (US 78).

Added to the applications router from lifecycle.py, so every path here sits under /applications
and checks the owner: another traveler's id is a 404, the same as an id that doesn't exist.

Reads the destination question files without changing them. Only answers to questions the new
questionnaire also asks, of the same type, are copied, and each is checked again by the engine
(`flow.reconcile`), so a copy can never hold an answer its own questionnaire would refuse.
"""

from collections.abc import Callable
from datetime import date, datetime, timezone
from typing import Annotated
import uuid

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session, select

from ..models import Application, ApplicationAnswer, Traveler
from . import flow
from .router import ApplicationSummary, _status, _summary, current_answers


class DuplicateChoice(BaseModel):
    """One visa the copy can be for, and the countries it can be for (the original's country left out)."""

    destination: str
    visa_type: str
    title: str
    destination_label: str
    visa_label: str
    # The question that names the country, such as "Which Schengen country is your main destination?".
    place_prompt: str | None
    places: list[flow.Option]


class DuplicateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    destination: str = Field(min_length=1, max_length=20)
    visa_type: str = Field(min_length=1, max_length=40)
    # An option of the new questionnaire's country question. Required when it has one.
    place: str | None = Field(default=None, min_length=1, max_length=40)


def _place_question(questionnaire: flow.Questionnaire) -> flow.Question | None:
    return questionnaire.question(questionnaire.summary.place) if questionnaire.summary.place else None


def _choices(source: flow.Questionnaire, source_place: str | None) -> list[DuplicateChoice]:
    choices = []
    for target in flow.available_questionnaires():
        question = _place_question(target)
        places = [option for option in (question.options or []) if question.type == "choice"] if question else []
        if target.id == source.id:
            # The same visa only makes sense for another country.
            places = [option for option in places if option.value != source_place]
            if not places:
                continue
        choices.append(DuplicateChoice(
            destination=target.destination, visa_type=target.visa_type, title=target.title,
            destination_label=target.destination_label, visa_label=target.visa_label,
            place_prompt=question.prompt if question else None, places=places,
        ))
    return choices


def copy_answers(source: flow.Questionnaire, target: flow.Questionnaire, answers: flow.Answers,
                 place: str | None, today: date) -> flow.Answers:
    """The answers that still apply in the target questionnaire, with the new country.

    Raises flow.InvalidAnswer when the country isn't one the target questionnaire offers.
    """
    place_question = _place_question(target)
    copied: flow.Answers = {}
    for question_id, value in answers.items():
        new, old = target.question(question_id), source.question(question_id)
        if new is None or old is None or new.type != old.type or (place_question and question_id == place_question.id):
            continue
        copied[question_id] = value
    if place_question is not None:
        copied[place_question.id] = flow.check_answer(place_question, place, {}, today)
    # Walks the target's questions in order: anything it would refuse, or no longer asks, is left out.
    kept, _ = flow.reconcile(target, copied, today)
    # A date checked against another answer (leaving after arriving) can't be checked once that answer
    # is gone, so the engine would keep it unchecked. Drop it too, and ask both again.
    lost = (set(answers) | set(copied)) - set(kept)
    while True:
        dependent = {question.id for question in target.questions() if question.id in kept
                     and any(rule.ref.partition(".")[0] in lost for rule in question.rules)}
        if not dependent:
            return kept
        kept, dropped = flow.reconcile(target, {key: value for key, value in kept.items() if key not in dependent}, today)
        lost |= dependent | set(dropped)


def add_record_routes(router: APIRouter, get_session, current_traveler, *,
                      today: Callable[[], date] = date.today) -> None:
    from ..auth import check_origin

    SessionDep = Annotated[Session, Depends(get_session)]
    TravelerDep = Annotated[Traveler, Depends(current_traveler)]

    def owned(session: Session, traveler: Traveler, application_id: str) -> Application:
        application = session.get(Application, application_id)
        if application is None or application.traveler_id != traveler.id:
            raise HTTPException(status_code=404, detail="Application not found.")
        return application

    def stored_rows(session: Session, application: Application) -> dict[str, ApplicationAnswer]:
        rows = session.exec(select(ApplicationAnswer).where(ApplicationAnswer.application_id == application.id))
        return {row.question_id: row for row in rows}

    @router.get("/{application_id}/duplicate", response_model=list[DuplicateChoice])
    def duplicate_choices(application_id: str, traveler: TravelerDep, session: SessionDep, response: Response):
        """Where a copy of this application can be for (US 78)."""
        application = owned(session, traveler, application_id)
        questionnaire = flow.load_questionnaire(application.questionnaire_id)
        rows = stored_rows(session, application)
        place_question = _place_question(questionnaire)
        place = rows[place_question.id].value if place_question and place_question.id in rows else None
        response.headers["Cache-Control"] = "private, no-store"
        return _choices(questionnaire, place)

    @router.post("/{application_id}/duplicate", response_model=ApplicationSummary, status_code=201,
                 dependencies=[Depends(check_origin)])
    def duplicate(application_id: str, data: DuplicateRequest, traveler: TravelerDep, session: SessionDep,
                  response: Response):
        """A new application for another country with the answers that still apply. The original is unchanged."""
        application = owned(session, traveler, application_id)
        source = flow.load_questionnaire(application.questionnaire_id)
        target = next((q for q in flow.available_questionnaires()
                       if (q.destination, q.visa_type) == (data.destination, data.visa_type)), None)
        if target is None:
            raise HTTPException(status_code=422, detail="We can't prepare that visa yet.")
        rows = stored_rows(session, application)
        stored = {key: row.value for key, row in rows.items()}
        place_question = _place_question(target)
        if place_question is None and data.place is not None:
            raise HTTPException(status_code=422, detail="This visa doesn't ask for a country.")
        if place_question is not None and data.place is None:
            raise HTTPException(status_code=422, detail="Choose the country for the new application.")
        if target.id == source.id and (place_question is None or data.place == stored.get(place_question.id)):
            raise HTTPException(status_code=422, detail="Choose a different country from the original application.")
        # Read only: what the original shows today (frozen if recorded as submitted).
        answers, _ = current_answers(source, application, stored, today())
        try:
            copied = copy_answers(source, target, answers, data.place, today())
        except flow.InvalidAnswer:
            raise HTTPException(status_code=422, detail="Choose one of the countries offered.") from None

        now = datetime.now(timezone.utc)
        new = Application(
            id=uuid.uuid4().hex, traveler_id=traveler.id, questionnaire_id=target.id,
            questionnaire_version=target.version, destination=target.destination, visa_type=target.visa_type,
            group_size=application.group_size, status=_status(target, copied), created_at=now, updated_at=now,
        )
        session.add(new)
        session.flush()
        for question_id, value in copied.items():
            # Each copy keeps where the answer came from; the new country is the traveler's own choice.
            from_original = place_question is None or question_id != place_question.id
            source_label = rows[question_id].source if from_original and question_id in rows else "traveler"
            session.add(ApplicationAnswer(application_id=new.id, question_id=question_id, value=value,
                                          source=source_label, answered_at=now))
        session.commit()
        response.headers["Cache-Control"] = "private, no-store"
        return ApplicationSummary(**_summary(new, target, copied))
