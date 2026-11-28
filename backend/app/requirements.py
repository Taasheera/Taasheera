"""Destination requirements written in the back-office, without a deploy (CMPS271-123).

Admins edit a destination's questionnaire (its questions and document list) and its visa guide facts (fees,
times, sources, `checked_on`), save a draft, check it and publish it with a change note. A publish or a
rollback adds a `RequirementRevision`; the newest one per questionnaire replaces the packaged files for
`flow.load_questionnaire` and the public visa guides. A rollback returns to any earlier revision or to the
packaged files. Nothing is published unless it passes `destination_check`, the packaged files' own checks,
and every publish and rollback is an audit event of the admin who made it.

Versions: a change to which questions are asked or which answers are accepted gets a questionnaire version
higher than any before it; a wording change keeps the version. Applications follow the current version:
the engine re-checks their answers on the next read, as after a deploy that changes a data file, and moves
them to it. Applications recorded as submitted keep their answers as they are.

Caches: `RequirementStore` keeps the newest revisions in memory. The process that publishes reloads at
once; every other process (the agent worker, or more API workers) re-reads the database at most every
REFRESH_SECONDS, so a publish reaches it within that time.
"""

import json
import logging
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, field_validator
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlmodel import Session, func, select

from .activity import record_activity
from .admin_api import require_role
from .applications import destination_check, flow, visa_guides
from .applications.destination_check import Change, Problem
from .auth import check_origin
from .features import require_feature
from .models import RECORDED_STATUSES, Application, RequirementDraft, RequirementRevision, Traveler

log = logging.getLogger(__name__)
REFRESH_SECONDS = 2.0
QuestionnaireId = Annotated[str, Path(pattern=r"^[a-z0-9-]+$", max_length=60)]
RevisionNumber = Annotated[int, Path(ge=0, le=1_000_000)]
STALE = "A newer version was published after you started. Reload it, then make your change again."
_UNREAD = object()


def latest_revisions(session: Session, questionnaire_id: str | None = None) -> list[RequirementRevision]:
    newest = (select(RequirementRevision.questionnaire_id, func.max(RequirementRevision.revision).label("revision"))
              .group_by(RequirementRevision.questionnaire_id))
    if questionnaire_id is not None:
        newest = newest.where(RequirementRevision.questionnaire_id == questionnaire_id)
    newest = newest.subquery()
    return list(session.exec(select(RequirementRevision).join(
        newest, (RequirementRevision.questionnaire_id == newest.c.questionnaire_id)
        & (RequirementRevision.revision == newest.c.revision))).all())


def content_of(questionnaire_id: str, row: RequirementRevision | None) -> tuple[dict, dict]:
    """A revision's questionnaire and guide JSON; the packaged files for revision 0 or a return to them."""
    if row is None or row.questionnaire is None:
        guide, checked_on = destination_check.packaged_guide(questionnaire_id)
        return destination_check.packaged_file(questionnaire_id), destination_check.guide_json(guide, checked_on)
    return row.questionnaire, row.guide


@dataclass(frozen=True)
class Served:
    """What travelers get from a revision."""
    questionnaire: flow.Questionnaire
    guide: visa_guides.GuideData
    checked_on: str


def effective(questionnaire_id: str, row: RequirementRevision | None) -> Served:
    packaged = flow.packaged_questionnaire(questionnaire_id)
    if row is None or row.questionnaire is None:
        guide, checked_on = destination_check.packaged_guide(questionnaire_id)
        version = max(packaged.version, row.questionnaire_version if row else 0)
        return Served(packaged.model_copy(update={"version": version}), guide, checked_on)
    questionnaire = flow.Questionnaire.model_validate_json(json.dumps(row.questionnaire))
    facts = dict(row.guide)
    checked_on = facts.pop("checked_on")
    return Served(questionnaire, visa_guides.GuideData.model_validate({**facts, "questionnaire_id": questionnaire_id}),
                  checked_on)


def served(questionnaire_id: str, row: RequirementRevision | None) -> tuple[RequirementRevision | None, Served]:
    """The revision in use and what it serves.

    A stored revision stops loading only after a deploy removed or reshaped something it relies on; the
    packaged files are served instead, never half of it.
    """
    try:
        return row, effective(questionnaire_id, row)
    except (KeyError, TypeError, ValueError, ValidationError):
        log.error("Published requirements for %s no longer load; serving the packaged files", questionnaire_id)
        return None, effective(questionnaire_id, None)


class RequirementStore:
    """The newest published revisions, installed into the questionnaire engine and the visa guides."""

    def __init__(self, engine, *, interval: float = REFRESH_SECONDS, clock=time.monotonic):
        self.engine, self.interval, self.clock = engine, interval, clock
        self._lock = threading.Lock()
        self._marker: Any = _UNREAD
        self._checked: float | None = None

    def install(self) -> None:
        flow.check_published = self.check
        self.reload()

    def uninstall(self) -> None:
        if flow.check_published == self.check:
            flow.check_published = lambda: None
            flow.PUBLISHED, visa_guides.PUBLISHED = {}, {}

    def check(self) -> None:
        """Re-read the published revisions when the last look is older than the interval."""
        if self._checked is not None and self.clock() - self._checked < self.interval:
            return
        if not self._lock.acquire(blocking=False):
            return
        try:
            self._refresh()
        finally:
            self._lock.release()

    def reload(self) -> None:
        """Read them now: at startup, and right after this process published."""
        with self._lock:
            self._marker = _UNREAD
            self._refresh()

    def _refresh(self) -> None:
        """Rows are only ever added, so the newest id and the count tell whether anything changed."""
        self._checked = self.clock()
        try:
            with Session(self.engine) as session:
                marker = tuple(session.exec(select(func.max(RequirementRevision.id), func.count())
                                            .select_from(RequirementRevision)).one())
                if marker == self._marker:
                    return
                rows = latest_revisions(session)
        except SQLAlchemyError:
            log.error("Could not read published requirements; keeping the ones already loaded")
            return
        questionnaires, guides = {}, {}
        for row in rows:
            try:
                kept, current = served(row.questionnaire_id, row)
            except KeyError:
                continue
            if kept is not None:
                questionnaires[row.questionnaire_id] = current.questionnaire
                guides[row.questionnaire_id] = (current.guide, current.checked_on)
        flow.PUBLISHED, visa_guides.PUBLISHED = questionnaires, guides
        self._marker = marker


class Content(BaseModel):
    questionnaire: dict[str, Any]
    guide: dict[str, Any]


class ContentRequest(Content):
    model_config = ConfigDict(extra="forbid")

    @field_validator("questionnaire", "guide")
    @classmethod
    def bounded(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(json.dumps(value)) > destination_check.MAX_BYTES:
            raise ValueError(f"Keep it under {destination_check.MAX_BYTES} bytes.")
        return value


class DraftRequest(ContentRequest):
    base_revision: int = Field(ge=0, le=1_000_000)


class PublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_revision: int = Field(ge=0, le=1_000_000)
    note: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class RollbackRequest(PublishRequest):
    """`revision` is the one to return to; 0 is the packaged files."""
    revision: int = Field(ge=0, le=1_000_000)


class CheckResult(BaseModel):
    """`changes` are against the version travelers see now; `questionnaire_version` is the one publishing
    would give, higher than any before when the questions or rules change."""
    valid: bool
    problems: list[Problem]
    changes: list[Change]
    questionnaire_version: int


class RevisionPublic(BaseModel):
    """`author` is the admin's name, None once their account is erased."""
    revision: int
    kind: Literal["publish", "rollback"]
    restored_revision: int | None
    questionnaire_version: int
    note: str
    author: str | None
    created_at: datetime


class DraftPublic(Content):
    base_revision: int
    author: str | None
    updated_at: datetime


class DestinationSummary(BaseModel):
    """`revision` is 0 while the packaged files are in use. Active applications are re-checked against a new
    version on their next read; recorded ones (submitted) are left as they are."""
    id: str
    title: str
    destination_label: str
    visa_label: str
    revision: int
    questionnaire_version: int
    packaged_version: int
    published_at: datetime | None
    has_draft: bool
    applications_active: int
    applications_recorded: int


class DestinationDetail(DestinationSummary):
    current: Content
    draft: DraftPublic | None
    history: list[RevisionPublic]


class DraftSaved(BaseModel):
    draft: DraftPublic
    check: CheckResult


@dataclass(frozen=True)
class Candidate:
    """Content checked against what travelers see now."""
    row: RequirementRevision | None
    report: CheckResult
    served: Served | None = None


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def create_requirements_router(get_session, current_traveler, store: RequirementStore, *,
                               today=date.today) -> APIRouter:
    admin = require_role(current_traveler, "admin")
    router = APIRouter(prefix="/admin/requirements", tags=["Admin requirements"],
                       dependencies=[Depends(require_feature("requirements_editor")), Depends(admin)])
    SessionDep = Annotated[Session, Depends(get_session)]
    AdminDep = Annotated[Traveler, Depends(admin)]

    def packaged(questionnaire_id: str) -> flow.Questionnaire:
        try:
            return flow.packaged_questionnaire(questionnaire_id)
        except KeyError:
            raise HTTPException(404, "We don't have that destination.") from None

    def current(session: Session, questionnaire_id: str) -> tuple[RequirementRevision | None, Served]:
        rows = latest_revisions(session, questionnaire_id)
        return served(questionnaire_id, rows[0] if rows else None)

    def names(session: Session, ids) -> dict[int, str]:
        wanted = {author for author in ids if author is not None}
        return dict(session.exec(select(Traveler.id, Traveler.name).where(Traveler.id.in_(wanted))).all()) if wanted else {}

    def application_counts(session: Session) -> dict[str, list[int]]:
        recorded = Application.status.in_(RECORDED_STATUSES)
        counts: dict[str, list[int]] = {}
        for questionnaire_id, is_recorded, total in session.exec(
                select(Application.questionnaire_id, recorded, func.count())
                .group_by(Application.questionnaire_id, recorded)).all():
            counts.setdefault(questionnaire_id, [0, 0])[1 if is_recorded else 0] += total
        return counts

    def summary(questionnaire: flow.Questionnaire, row: RequirementRevision | None, now: Served,
                has_draft: bool, counts: dict[str, list[int]]) -> dict:
        active, recorded = counts.get(questionnaire.id, [0, 0])
        return dict(id=questionnaire.id, title=now.questionnaire.title,
                    destination_label=now.questionnaire.destination_label, visa_label=now.questionnaire.visa_label,
                    revision=row.revision if row else 0, questionnaire_version=now.questionnaire.version,
                    packaged_version=questionnaire.version, published_at=_utc(row.created_at) if row else None,
                    has_draft=has_draft, applications_active=active, applications_recorded=recorded)

    def draft_public(session: Session, draft: RequirementDraft) -> DraftPublic:
        return DraftPublic(questionnaire=draft.questionnaire, guide=draft.guide, base_revision=draft.base_revision,
                           author=names(session, [draft.author_id]).get(draft.author_id),
                           updated_at=_utc(draft.updated_at))

    def detail(session: Session, questionnaire_id: str) -> DestinationDetail:
        questionnaire = packaged(questionnaire_id)
        row, now = current(session, questionnaire_id)
        draft = session.get(RequirementDraft, questionnaire_id)
        history = session.exec(select(RequirementRevision).where(RequirementRevision.questionnaire_id == questionnaire_id)
                               .order_by(RequirementRevision.revision.desc())).all()
        authors = names(session, [revision.author_id for revision in history])
        questionnaire_data, guide_data = content_of(questionnaire_id, row)
        return DestinationDetail(
            **summary(questionnaire, row, now, draft is not None, application_counts(session)),
            current=Content(questionnaire={**questionnaire_data, "version": now.questionnaire.version}, guide=guide_data),
            draft=draft_public(session, draft) if draft else None,
            history=[RevisionPublic(revision=revision.revision, kind=revision.kind,
                                    restored_revision=revision.restored_revision,
                                    questionnaire_version=revision.questionnaire_version, note=revision.note,
                                    author=authors.get(revision.author_id), created_at=_utc(revision.created_at))
                     for revision in history],
        )

    def next_version(session: Session, questionnaire_id: str, now: flow.Questionnaire,
                     candidate: flow.Questionnaire) -> int:
        """The same version for wording changes; otherwise one higher than any this questionnaire ever had."""
        if destination_check.structure(candidate) == destination_check.structure(now):
            return now.version
        published = session.exec(select(func.max(RequirementRevision.questionnaire_version))
                                 .where(RequirementRevision.questionnaire_id == questionnaire_id)).one() or 0
        return max(published, now.version, flow.packaged_questionnaire(questionnaire_id).version) + 1

    def candidate(session: Session, questionnaire_id: str, questionnaire: Any, guide: Any) -> Candidate:
        row, now = current(session, questionnaire_id)
        result = destination_check.check(questionnaire_id, questionnaire, guide, version=now.questionnaire.version,
                                         today=today())
        if result.problems:
            questionnaire_before, guide_before = content_of(questionnaire_id, row)
            found = (destination_check.changes("questionnaire", questionnaire_before, questionnaire)
                     + destination_check.changes("guide", guide_before, guide))
            return Candidate(row, CheckResult(valid=False, problems=result.problems,
                                              changes=[change for change in found if change.path != "version"],
                                              questionnaire_version=now.questionnaire.version))
        version = next_version(session, questionnaire_id, now.questionnaire, result.questionnaire)
        after = Served(result.questionnaire.model_copy(update={"version": version}), result.guide, result.checked_on)
        found = (destination_check.changes("questionnaire", destination_check.questionnaire_json(now.questionnaire),
                                           destination_check.questionnaire_json(after.questionnaire))
                 + destination_check.changes("guide", destination_check.guide_json(now.guide, now.checked_on),
                                             destination_check.guide_json(after.guide, after.checked_on)))
        return Candidate(row, CheckResult(valid=True, problems=[], questionnaire_version=version,
                                          changes=[change for change in found if change.path != "version"]), after)

    def refuse_problems(report: CheckResult, message: str) -> None:
        if not report.valid:
            raise HTTPException(422, {"message": message, "problems": [problem.model_dump() for problem in report.problems]})
        if not report.changes:
            raise HTTPException(409, "Nothing would change from the version travelers see now.")

    def add_revision(session: Session, request: Request, actor: Traveler, revision: RequirementRevision,
                     event_type: str) -> None:
        """record_activity commits: the revision and its audit event are saved together or not at all."""
        session.add(revision)
        try:
            recorded = record_activity(session, request.app.state.activity_directory, actor.id, event_type,
                                       f"{revision.questionnaire_id}:r{revision.revision}", "success")
        except IntegrityError:
            session.rollback()
            raise HTTPException(409, STALE) from None
        if recorded is None:
            raise HTTPException(401, "Your account is no longer available.")
        store.reload()

    @router.get("", response_model=list[DestinationSummary])
    def list_destinations(session: SessionDep):
        rows = {row.questionnaire_id: row for row in latest_revisions(session)}
        drafts = set(session.exec(select(RequirementDraft.questionnaire_id)).all())
        counts = application_counts(session)
        listed = []
        for path in sorted(flow.QUESTIONNAIRE_DIR.glob("*.json")):
            questionnaire = flow.packaged_questionnaire(path.stem)
            row, now = served(questionnaire.id, rows.get(questionnaire.id))
            listed.append(summary(questionnaire, row, now, questionnaire.id in drafts, counts))
        return listed

    @router.get("/{questionnaire_id}", response_model=DestinationDetail)
    def get_destination(questionnaire_id: QuestionnaireId, session: SessionDep):
        return detail(session, questionnaire_id)

    @router.get("/{questionnaire_id}/revisions/{revision}", response_model=Content)
    def get_revision(questionnaire_id: QuestionnaireId, revision: RevisionNumber, session: SessionDep):
        packaged(questionnaire_id)
        row = None
        if revision:
            row = session.exec(select(RequirementRevision).where(RequirementRevision.questionnaire_id == questionnaire_id,
                                                                 RequirementRevision.revision == revision)).first()
            if row is None:
                raise HTTPException(404, "That revision doesn't exist.")
        questionnaire, guide = content_of(questionnaire_id, row)
        return Content(questionnaire=questionnaire, guide=guide)

    @router.post("/{questionnaire_id}/check", response_model=CheckResult, dependencies=[Depends(check_origin)])
    def check_content(questionnaire_id: QuestionnaireId, data: ContentRequest, session: SessionDep):
        packaged(questionnaire_id)
        return candidate(session, questionnaire_id, data.questionnaire, data.guide).report

    @router.put("/{questionnaire_id}/draft", response_model=DraftSaved, dependencies=[Depends(check_origin)])
    def save_draft(questionnaire_id: QuestionnaireId, data: DraftRequest, session: SessionDep, signed_in: AdminDep):
        """One shared draft per destination: the last admin to save it is its author."""
        packaged(questionnaire_id)
        draft = session.get(RequirementDraft, questionnaire_id) or RequirementDraft(
            questionnaire_id=questionnaire_id, questionnaire={}, guide={}, base_revision=data.base_revision)
        draft.questionnaire, draft.guide, draft.base_revision = data.questionnaire, data.guide, data.base_revision
        draft.author_id, draft.updated_at = signed_in.id, datetime.now(timezone.utc)
        session.add(draft)
        session.commit()
        session.refresh(draft)
        return DraftSaved(draft=draft_public(session, draft),
                          check=candidate(session, questionnaire_id, data.questionnaire, data.guide).report)

    @router.delete("/{questionnaire_id}/draft", status_code=204, response_class=Response,
                   dependencies=[Depends(check_origin)])
    def discard_draft(questionnaire_id: QuestionnaireId, session: SessionDep):
        packaged(questionnaire_id)
        draft = session.get(RequirementDraft, questionnaire_id)
        if draft is None:
            raise HTTPException(404, "There is no draft for this destination.")
        session.delete(draft)
        session.commit()

    @router.post("/{questionnaire_id}/publish", response_model=DestinationDetail, dependencies=[Depends(check_origin)])
    def publish(questionnaire_id: QuestionnaireId, data: PublishRequest, request: Request, session: SessionDep,
                signed_in: AdminDep):
        packaged(questionnaire_id)
        draft = session.get(RequirementDraft, questionnaire_id)
        if draft is None:
            raise HTTPException(409, "Save your changes as a draft before publishing.")
        checked = candidate(session, questionnaire_id, draft.questionnaire, draft.guide)
        revision = checked.row.revision if checked.row else 0
        if data.base_revision != revision or draft.base_revision != revision:
            raise HTTPException(409, STALE)
        refuse_problems(checked.report, "Fix these problems before publishing.")
        session.delete(draft)
        add_revision(session, request, signed_in, RequirementRevision(
            questionnaire_id=questionnaire_id, revision=revision + 1, kind="publish",
            questionnaire=destination_check.questionnaire_json(checked.served.questionnaire),
            guide=destination_check.guide_json(checked.served.guide, checked.served.checked_on),
            questionnaire_version=checked.report.questionnaire_version, note=data.note, author_id=signed_in.id),
            "requirements_publish")
        return detail(session, questionnaire_id)

    @router.post("/{questionnaire_id}/rollback", response_model=DestinationDetail, dependencies=[Depends(check_origin)])
    def rollback(questionnaire_id: QuestionnaireId, data: RollbackRequest, request: Request, session: SessionDep,
                 signed_in: AdminDep):
        packaged(questionnaire_id)
        rows = latest_revisions(session, questionnaire_id)
        revision = rows[0].revision if rows else 0
        if data.base_revision != revision:
            raise HTTPException(409, STALE)
        if data.revision == revision:
            raise HTTPException(409, "That revision is the one travelers see now.")
        target = None
        if data.revision:
            target = session.exec(select(RequirementRevision).where(
                RequirementRevision.questionnaire_id == questionnaire_id,
                RequirementRevision.revision == data.revision)).first()
            if target is None:
                raise HTTPException(404, "That revision doesn't exist.")
        checked = candidate(session, questionnaire_id, *content_of(questionnaire_id, target))
        refuse_problems(checked.report, "That version no longer passes the checks, so it can't come back.")
        packaged_files = target is None or target.questionnaire is None
        add_revision(session, request, signed_in, RequirementRevision(
            questionnaire_id=questionnaire_id, revision=revision + 1, kind="rollback", restored_revision=data.revision,
            questionnaire=None if packaged_files else destination_check.questionnaire_json(checked.served.questionnaire),
            guide=None if packaged_files else destination_check.guide_json(checked.served.guide,
                                                                           checked.served.checked_on),
            questionnaire_version=checked.report.questionnaire_version, note=data.note, author_id=signed_in.id),
            "requirements_rollback")
        return detail(session, questionnaire_id)

    return router
