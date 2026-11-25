"""Privacy policy acceptance (CMPS271-115, US 30).

A traveler accepts the current policy version before uploading any identity document. Each
acceptance is saved with its version and time; publishing a new version (change
PRIVACY_POLICY_VERSION together with the date in frontend/public/privacy.html) asks everyone
again on their next visit. Behind the `privacy_consent` flag: while it is off, the routes answer
404 and uploads work as before.
"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from .features import require_feature
from .models import PrivacyConsent, Traveler

# The policy's "Last updated" date. Must match the <time datetime> in frontend/public/privacy.html.
PRIVACY_POLICY_VERSION = "2026-11-25"
CONSENT_REQUIRED = "Please read and accept the privacy policy before uploading documents."
# Lets the frontend tell this refusal apart from other 403s and show the policy again.
CONSENT_HEADER = {"X-Privacy-Consent": "required"}


class ConsentStatus(BaseModel):
    policy_version: str
    accepted: bool
    accepted_at: datetime | None


class AcceptPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # The version the traveler was shown, so an acceptance never covers text they did not see.
    policy_version: str = Field(min_length=1, max_length=20)


def accepted_current(session: Session, traveler_id: int) -> PrivacyConsent | None:
    return session.exec(select(PrivacyConsent).where(PrivacyConsent.traveler_id == traveler_id,
                                                     PrivacyConsent.policy_version == PRIVACY_POLICY_VERSION)).first()


def create_consent_requirement(get_session, current_traveler):
    """A route dependency refusing uploads (403) until the traveler accepts the current policy.

    Does nothing while the `privacy_consent` flag is off.
    """

    def require_consent(request: Request, traveler: Annotated[Traveler, Depends(current_traveler)],
                        session: Annotated[Session, Depends(get_session)]) -> None:
        if not request.app.state.feature_flags.is_on("privacy_consent"):
            return
        if accepted_current(session, traveler.id) is None:
            raise HTTPException(status_code=403, detail=CONSENT_REQUIRED, headers=CONSENT_HEADER)

    return require_consent


def create_consent_router(get_session, current_traveler) -> APIRouter:
    from .auth import check_origin

    router = APIRouter(prefix="/profile/privacy-consent", tags=["Privacy policy"],
                       dependencies=[Depends(require_feature("privacy_consent"))])
    SessionDep = Annotated[Session, Depends(get_session)]
    TravelerDep = Annotated[Traveler, Depends(current_traveler)]

    def status(row: PrivacyConsent | None) -> ConsentStatus:
        return ConsentStatus(policy_version=PRIVACY_POLICY_VERSION, accepted=row is not None,
                             accepted_at=row.accepted_at if row else None)

    @router.get("", response_model=ConsentStatus)
    def get_consent(traveler: TravelerDep, session: SessionDep, response: Response):
        """Whether the signed-in traveler accepted the current policy version."""
        response.headers["Cache-Control"] = "private, no-store"
        return status(accepted_current(session, traveler.id))

    @router.post("", response_model=ConsentStatus, dependencies=[Depends(check_origin)])
    def accept(body: AcceptPolicy, traveler: TravelerDep, session: SessionDep, response: Response):
        """Accept the current policy version. Accepting again keeps the first acceptance time."""
        response.headers["Cache-Control"] = "private, no-store"
        if body.policy_version != PRIVACY_POLICY_VERSION:
            raise HTTPException(status_code=409, detail="The privacy policy has changed. Reload the page to read the new version.")
        row = accepted_current(session, traveler.id)
        if row is None:
            session.add(PrivacyConsent(traveler_id=traveler.id, policy_version=PRIVACY_POLICY_VERSION))
            try:
                session.commit()
            except IntegrityError:
                # Another tab accepted at the same moment; theirs is the record.
                session.rollback()
            row = accepted_current(session, traveler.id)
        return status(row)

    return router
