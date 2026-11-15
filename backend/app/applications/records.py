"""Duplicate an application for another country (US 78) and export its record as a file (US 80).

Added to the applications router from lifecycle.py, so every path here sits under /applications
and checks the owner: another traveler's id is a 404, the same as an id that doesn't exist.

Reads the destination question files without changing them. Only answers to questions the new
questionnaire also asks, of the same type, are copied, and each is checked again by the engine
(`flow.reconcile`), so a copy can never hold an answer its own questionnaire would refuse.

The export is the traveler's own copy, independent of the service: the answers as the page shows
them, the status and the timeline, as a JSON file. It holds passport details from the answers, so
it is owner-only, never cached, and each download is written to the activity log.
"""

from collections.abc import Callable
from datetime import date, datetime, timezone
import re
from typing import Annotated, Any, Literal
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session, select

from ..activity import record_activity
from ..models import Application, ApplicationAnswer, Traveler
from . import flow
from .router import ApplicationSummary, _status, _summary, _utc, current_answers


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


EXPORT_NOTICE = ("Your own copy of a visa application prepared with Taasheera. It holds your passport details: "
                 "keep it somewhere private. Taasheera does not give legal or immigration advice, and nothing "
                 "was submitted to an embassy by Taasheera.")


class ExportedAnswer(BaseModel):
    question_id: str
    step: str
    question: str
    value: Any
    # The option's wording for choices and yes/no questions, as the page shows it.
    label: str | None
    source: str
    answered_at: datetime


class ExportedRecord(BaseModel):
    format: Literal["taasheera.application-record"] = "taasheera.application-record"
    version: Literal[1] = 1
    exported_at: datetime
    notice: str
    application: ApplicationSummary
    questionnaire_id: str
    questionnaire_version: int
    answers: list[ExportedAnswer]
    # The same entries and next step as the timeline on the application's page (US 20).
    timeline: Any


def _label(question: flow.Question, value: Any) -> str | None:
    if question.type == "yes_no" and isinstance(value, bool):
        return "Yes" if value else "No"
    if question.type == "choice":
        return next((option.label for option in question.options or [] if option.value == value), None)
    return None


def export_filename(summary: ApplicationSummary, today: date) -> str:
    """ "taasheera-france-2026-11-15.json": ASCII only, so every browser saves it under that name."""
    name = re.sub(r"[^a-z0-9]+", "-", (summary.place or summary.destination_label).lower()).strip("-") or "application"
    return f"taasheera-{name[:40]}-{today.isoformat()}.json"


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

    @router.get("/{application_id}/export", response_class=Response,
                responses={200: {"content": {"application/json": {}}, "description": "The record as a JSON file."}})
    def export(application_id: str, traveler: TravelerDep, session: SessionDep, request: Request):
        """The application's answers, status and timeline as a file the traveler keeps (US 80)."""
        from .lifecycle import build_timeline

        application = owned(session, traveler, application_id)
        questionnaire = flow.load_questionnaire(application.questionnaire_id)
        rows = stored_rows(session, application)
        # As the page shows them: re-checked against today, or frozen once recorded. Read only.
        answers, _ = current_answers(questionnaire, application, {key: row.value for key, row in rows.items()}, today())
        summary = ApplicationSummary(**_summary(application, questionnaire, answers))
        record = ExportedRecord(
            exported_at=datetime.now(timezone.utc), notice=EXPORT_NOTICE, application=summary,
            questionnaire_id=questionnaire.id, questionnaire_version=application.questionnaire_version,
            answers=[ExportedAnswer(question_id=question.id, step=questionnaire.step_of(question.id).title,
                                    question=question.prompt, value=answers[question.id],
                                    label=_label(question, answers[question.id]), source=rows[question.id].source,
                                    answered_at=_utc(rows[question.id].answered_at))
                     for question in flow.visible_questions(questionnaire, answers) if question.id in answers],
            timeline=build_timeline(session, application, today()).model_dump(mode="json"),
        )
        # Written before the file leaves, like passport access: no record, no download.
        if record_activity(session, request.app.state.activity_directory, traveler.id,
                           "application_export", "application", "success") is None:
            raise HTTPException(status_code=401, detail="Your account is no longer available.")
        filename = export_filename(summary, today())
        return Response(content=record.model_dump_json(indent=2), media_type="application/json", headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        })

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
