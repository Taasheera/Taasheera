"""Archive, delete, the embassy decision and the timeline of one application (US 79, US 55, US 20).

Added to the applications router, so every path here sits under
/applications (and logs with that full route template) and has its owner check: another traveler's id is a 404,
the same as an id that doesn't exist.

The timeline is worked out from what is already stored (when the application
was started, when each answer was last saved, when it was archived, submitted
and decided) instead of a separate event table, so deleting an application
leaves nothing behind.

Submitting and the embassy's decision are recorded by the traveler (US 55): the
agent never submits anything. A recorded application's answers are frozen.
"""

from collections.abc import Callable
from datetime import date, datetime, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict
from sqlmodel import Session, delete, select, update

from ..models import (Application, ApplicationAnswer, ApplicationTracking, AssistantMessage, GeneratedDocument, Notification, Payment, SupportAccessGrant,
                      SupportRequest, SupportingDocument, Traveler)
from ..notifications import notify_status_change
from . import flow
from .router import ApplicationSummary, _status, _summary, _utc, current_answers, is_recorded


class TimelineEvent(BaseModel):
    kind: Literal["started", "answered", "stopped", "complete", "prepared", "appointment", "submitted", "approved",
                  "refused", "archived"]
    at: datetime
    text: str
    # For answers: which question, and the step it belongs to, so the page can group them.
    question_id: str | None = None
    step_title: str | None = None


class NextStep(BaseModel):
    kind: Literal["answer", "change", "complete", "submitted", "approved", "refused", "archived"]
    text: str


class DecisionRequest(BaseModel):
    """What the traveler records (US 55). ANSWERS_COMPLETE takes back a submission recorded by mistake.

    Named `record`, not `status`: request bodies outside /admin never carry a field called status or role.
    """

    model_config = ConfigDict(extra="forbid")
    record: Literal["SUBMITTED", "APPROVED", "REFUSED", "ANSWERS_COMPLETE"]


# Where each status can go next. Submitting needs every question answered; a decision needs a
# submission first; a wrong click on either can be taken back one step.
DECISION_STEPS = {
    "ANSWERS_COMPLETE": {"SUBMITTED"},
    "SUBMITTED": {"APPROVED", "REFUSED", "ANSWERS_COMPLETE"},
    "APPROVED": {"REFUSED", "SUBMITTED"},
    "REFUSED": {"APPROVED", "SUBMITTED"},
}
DECISION_REFUSED = {
    "SUBMITTED": "Answer every question before recording that you submitted this application.",
    "APPROVED": "Record that you submitted this application before recording the embassy's decision.",
    "REFUSED": "Record that you submitted this application before recording the embassy's decision.",
    "ANSWERS_COMPLETE": "This application isn't recorded as submitted.",
}
WITHDRAW_REFUSED = ("This application's answers no longer pass today's checks (for example, the trip date has "
                    "passed), so it stays recorded as submitted. Start a new application, or duplicate this one for "
                    "another country.")


class Timeline(BaseModel):
    id: str
    archived_at: datetime | None
    # Oldest first. An answer changed later shows once, at the time it was last saved.
    events: list[TimelineEvent]
    next: NextStep


def delete_application(session: Session, application: Application) -> list[str]:
    """Remove the conversation, answers, documents, agent runs and payments before their application (foreign keys on all).

    Returns the supporting document files no other application uses, which go once the deletion is committed.
    """
    from ..agent.queue import delete_application_runs
    from ..documents.usage import FILE_KEY, unused_files

    delete_application_runs(session, application.id)
    file_keys = list(session.exec(select(FILE_KEY).where(SupportingDocument.application_id == application.id)).all())
    session.exec(delete(SupportingDocument).where(SupportingDocument.application_id == application.id))
    document_ids = unused_files(session, file_keys)
    session.exec(delete(AssistantMessage).where(AssistantMessage.application_id == application.id))
    session.exec(delete(Notification).where(Notification.application_id == application.id))
    session.exec(delete(SupportAccessGrant).where(SupportAccessGrant.application_id == application.id))
    session.exec(update(SupportRequest).where(SupportRequest.application_id == application.id).values(application_id=None))
    session.exec(delete(ApplicationAnswer).where(ApplicationAnswer.application_id == application.id))
    session.exec(delete(Payment).where(Payment.application_id == application.id))
    session.exec(delete(GeneratedDocument).where(GeneratedDocument.application_id == application.id))
    session.exec(delete(ApplicationTracking).where(ApplicationTracking.application_id == application.id))
    from .group import delete_group_rows
    delete_group_rows(session, application)
    session.delete(application)
    return document_ids


def build_timeline(session: Session, application: Application, today: date) -> Timeline:
    """Everything that has happened to one application, and what comes next (US 20).

    Shared by the timeline page and the exported record (US 80), so both say the same thing.
    """
    questionnaire = flow.load_questionnaire(application.questionnaire_id)
    rows = {row.question_id: row for row in session.exec(
        select(ApplicationAnswer).where(ApplicationAnswer.application_id == application.id))}
    # Checked like the chat does, but only read: answers that no longer apply are left out, not deleted.
    # Recorded ones are shown frozen, as they were submitted.
    answers, _ = current_answers(questionnaire, application, {key: row.value for key, row in rows.items()}, today)
    recorded = is_recorded(application)
    status = "ANSWERS_COMPLETE" if recorded else _status(questionnaire, answers)

    events = [TimelineEvent(kind="started", at=_utc(application.created_at),
                            text=f"Started the {questionnaire.title} application")]
    answered = [question for question in flow.visible_questions(questionnaire, answers) if question.id in answers]
    events += [TimelineEvent(kind="answered", at=_utc(rows[question.id].answered_at), text=question.prompt,
                             question_id=question.id, step_title=questionnaire.step_of(question.id).title)
               for question in answered]
    last_answer = max((_utc(rows[question.id].answered_at) for question in answered), default=None)
    stopped = flow.stop_message(questionnaire, answers)
    if status == "INELIGIBLE" and last_answer:
        events.append(TimelineEvent(kind="stopped", at=last_answer, text=stopped or "An answer stopped the application."))
    elif status == "ANSWERS_COMPLETE" and last_answer:
        events.append(TimelineEvent(kind="complete", at=last_answer, text="Every question answered"))
    from .tracking import next_step_text, timeline_events
    events += [TimelineEvent(kind=kind, at=at, text=text) for kind, at, text in timeline_events(session, application)]
    if recorded and application.submitted_at:
        events.append(TimelineEvent(kind="submitted", at=_utc(application.submitted_at),
                                    text="You recorded submitting it to the embassy"))
    if application.status in ("APPROVED", "REFUSED") and application.decided_at:
        decision = application.status.lower()
        events.append(TimelineEvent(kind=decision, at=_utc(application.decided_at),
                                    text=f"You recorded that the embassy {decision} it"))
    if application.archived_at:
        events.append(TimelineEvent(kind="archived", at=_utc(application.archived_at), text="Archived"))
    # Stable sort: answers saved in the same instant keep the questionnaire's order.
    events.sort(key=lambda event: event.at)

    current = flow.next_question(questionnaire, answers)
    if application.archived_at:
        next_step = NextStep(kind="archived", text="This application is archived. Restore it to see it on your "
                                                   "dashboard again." if recorded else
                             "This application is archived. Restore it to pick up where you left off.")
    elif application.status == "SUBMITTED":
        next_step = NextStep(kind="submitted", text="Waiting for the embassy's decision. When you hear back, record "
                                                    "whether the visa was approved or refused.")
    elif application.status == "APPROVED":
        next_step = NextStep(kind="approved", text="The embassy approved this application. Keep this record with "
                                                   "your travel documents.")
    elif application.status == "REFUSED":
        next_step = NextStep(kind="refused", text="The embassy refused this application. Its letter gives the "
                                                  "reasons; you can start a new application whenever you're ready.")
    elif status == "INELIGIBLE":
        # The reason itself is the timeline's last entry; this says what to do about it.
        next_step = NextStep(kind="change", text="An answer stopped this application. Change that answer, or "
                                                 "start an application for a different visa.")
    elif current:
        step = questionnaire.step_of(current.id)
        number = questionnaire.steps.index(step) + 1
        next_step = NextStep(kind="answer", text=f"Next question (step {number} of {len(questionnaire.steps)}, "
                                                 f"{step.title}): {current.prompt}")
    else:
        next_step = NextStep(kind="complete", text=next_step_text(session, application, today) or (
            "Your answers are complete. Supporting documents and a final review come next; nothing is submitted "
            "without your confirmation. Once you hand it to the embassy yourself, record it here."))
    return Timeline(id=application.id, archived_at=_utc(application.archived_at) if application.archived_at else None,
                    events=events, next=next_step)


def add_lifecycle_routes(
    router: APIRouter,
    get_session,
    current_traveler,
    *,
    today: Callable[[], date] = date.today,
    remove_document_files: Callable[[int, list[str]], None] = lambda traveler_id, document_ids: None,
    remove_agent_files: Callable[[int, str], None] = lambda traveler_id, application_id: None,
) -> None:
    from ..auth import check_origin
    from .records import add_record_routes

    SessionDep = Annotated[Session, Depends(get_session)]
    TravelerDep = Annotated[Traveler, Depends(current_traveler)]
    # Duplicate and export (US 78, US 80) live in their own file, and so does what comes after the answers.
    add_record_routes(router, get_session, current_traveler, today=today)
    from .tracking import add_tracking_routes
    add_tracking_routes(router, get_session, current_traveler, today=today)
    from .group import add_group_routes
    add_group_routes(router, get_session, current_traveler, today=today)

    def owned(session: Session, traveler: Traveler, application_id: str) -> Application:
        application = session.get(Application, application_id)
        if application is None or application.traveler_id != traveler.id:
            raise HTTPException(status_code=404, detail="Application not found.")
        return application

    def stored_rows(session: Session, application: Application) -> dict[str, ApplicationAnswer]:
        rows = session.exec(select(ApplicationAnswer).where(ApplicationAnswer.application_id == application.id))
        return {row.question_id: row for row in rows}

    def summary(session: Session, application: Application) -> ApplicationSummary:
        questionnaire = flow.load_questionnaire(application.questionnaire_id)
        stored = {key: row.value for key, row in stored_rows(session, application).items()}
        # As the page shows them: re-checked against today, or frozen once recorded.
        answers, _ = current_answers(questionnaire, application, stored, today())
        result = ApplicationSummary(**_summary(application, questionnaire, answers))
        if not is_recorded(application):
            # The status those answers give, so it agrees with the count (an archived row isn't re-saved).
            result.status = _status(questionnaire, answers)
        return result

    def set_archived(application_id: str, traveler: Traveler, session: Session, response: Response,
                     archived: bool) -> ApplicationSummary:
        application = owned(session, traveler, application_id)
        # Doing it twice changes nothing. `updated_at` stays as it is: it means the last change to the answers.
        if (application.archived_at is not None) != archived:
            application.archived_at = datetime.now(timezone.utc) if archived else None
            session.add(application)
            session.commit()
        response.headers["Cache-Control"] = "private, no-store"
        return summary(session, application)

    @router.post("/{application_id}/archive", response_model=ApplicationSummary,
                 dependencies=[Depends(check_origin)])
    def archive(application_id: str, traveler: TravelerDep, session: SessionDep, response: Response):
        return set_archived(application_id, traveler, session, response, True)

    @router.post("/{application_id}/unarchive", response_model=ApplicationSummary,
                 dependencies=[Depends(check_origin)])
    def unarchive(application_id: str, traveler: TravelerDep, session: SessionDep, response: Response):
        return set_archived(application_id, traveler, session, response, False)

    def status_today(session: Session, application: Application) -> str:
        """The status the answers give today: a trip date that has passed since the last visit can undo "complete"."""
        questionnaire = flow.load_questionnaire(application.questionnaire_id)
        stored = {key: row.value for key, row in stored_rows(session, application).items()}
        return _status(questionnaire, flow.reconcile(questionnaire, stored, today())[0])

    @router.post("/{application_id}/decision", response_model=ApplicationSummary,
                 dependencies=[Depends(check_origin)])
    def record_decision(application_id: str, data: DecisionRequest, traveler: TravelerDep, session: SessionDep,
                        response: Response):
        """The traveler records submitting the application, then the embassy's decision (US 55)."""
        application = owned(session, traveler, application_id)
        if application.archived_at is not None:
            raise HTTPException(status_code=409, detail="This application is archived. Restore it from your dashboard first.")
        previous = application.status
        current = previous if is_recorded(application) else status_today(session, application)
        response.headers["Cache-Control"] = "private, no-store"
        target = data.record
        if target == previous and is_recorded(application) or (target == "ANSWERS_COMPLETE" and not is_recorded(application)
                                                                and current == "ANSWERS_COMPLETE"):
            # Recording the same thing twice (a double click) changes nothing.
            return summary(session, application)
        if target not in DECISION_STEPS.get(current, set()):
            raise HTTPException(status_code=409, detail=DECISION_REFUSED[target])

        now = datetime.now(timezone.utc)
        if target == "ANSWERS_COMPLETE":
            # Taken back: answering resumes. Only while the answers still pass today's checks (Finding 3):
            # otherwise the submission couldn't be recorded again and its date would be lost.
            if status_today(session, application) != "ANSWERS_COMPLETE":
                raise HTTPException(status_code=409, detail=WITHDRAW_REFUSED)
            application.status = "ANSWERS_COMPLETE"
            application.submitted_at = application.decided_at = None
        elif target == "SUBMITTED":
            # From a decision this clears only the decision: the submission date stays.
            if previous not in ("APPROVED", "REFUSED"):
                application.submitted_at = now
            application.status, application.decided_at = "SUBMITTED", None
        else:
            application.status, application.decided_at = target, now
        # `updated_at` stays as it is: it means the last change to the answers, as for archiving.
        notify_status_change(session, application, previous)
        session.add(application)
        session.commit()
        return summary(session, application)

    @router.delete("/{application_id}", status_code=204, dependencies=[Depends(check_origin)])
    def remove(application_id: str, traveler: TravelerDep, session: SessionDep):
        # Passport uploads stay: they belong to the passport page, and answers hold their own copy.
        document_ids = delete_application(session, owned(session, traveler, application_id))
        session.commit()
        remove_document_files(traveler.id, document_ids)
        remove_agent_files(traveler.id, application_id)
        return Response(status_code=204, headers={"Cache-Control": "private, no-store"})

    @router.get("/{application_id}/timeline", response_model=Timeline)
    def timeline(application_id: str, traveler: TravelerDep, session: SessionDep, response: Response):
        application = owned(session, traveler, application_id)
        response.headers["Cache-Control"] = "private, no-store"
        return build_timeline(session, application, today())
