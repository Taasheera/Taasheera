"""Feature flags (US 127): merge unfinished work without showing it on the live site.

A flag is on by default in development and off by default in production, so a merged but
untested feature works locally and stays hidden on the live site. A flag in RELEASED has passed
testing and defaults on in production too; it stays a flag for now so FEATURE_<NAME>=false can
switch it off on the live site without a release. Set FEATURE_<NAME>=true
or false in the server's environment to override the default (for example
FEATURE_SUPPORTING_DOCUMENTS=true in /etc/taasheera/app.env once the feature has passed testing).
The frontend reads the same values from GET /features.
"""

import os
from dataclasses import dataclass

from fastapi import APIRouter, HTTPException, Request, Response

# Every flag the app knows, with the work it hides. To add a flag, add it here and to
# FeatureName in frontend/src/features.ts. Delete it in both places once the feature is released.
FLAGS = {
    "passport_scan_quality": "Detect unreadable passport scans before OCR (CMPS271-30)",
    "embassy_pdf": "Review and download the official embassy PDF (CMPS271-64/65/66/124)",
    "agent_form_filling": "The agent fills an embassy online form (CMPS271-67)",
    "supporting_documents": "Supporting document upload (CMPS271-29)",
    "privacy_consent": "Accept the privacy policy before uploading documents (CMPS271-115)",
    "email_notifications": "Email copies of notifications and reminders (CMPS271-98)",
    "visa_guide": "Public guide for each supported visa: who needs it, documents, fees, times (CMPS271-19/20/21/24)",
    "arabic": "Switch the home page, FAQ and site header between English and Arabic (CMPS271-16)",
    "malware_scan": "Scan every uploaded file for malware before it is stored (CMPS271-31)",
    "payments": "Price breakdown, simulated payment before submission, and receipts (CMPS271-106/108/109)",
    "requirements_editor": "Admins publish destination requirements without a deploy (CMPS271-123)",
    "kpi_dashboard": "Admin KPI page: usage, completion and agent success (CMPS271-140)",
    "passport_rules": "Passport validity and blank pages checked per destination (CMPS271-32)",
    "photo_check": "Passport photos checked against each destination's photo specs (CMPS271-33)",
    "document_vault": "Document vault: reuse and delete uploaded documents (CMPS271-34/35)",
    "document_translation": "Machine translation of a supporting document's text (CMPS271-37)",
    "support_tools": "Support access with consent, talking to a person and editable FAQ (CMPS271-128/92/129)",
    "generated_documents": "Cover letter, itinerary and letter templates written from the answers (CMPS271-57 to 62)",
    "submission_tracking": "Submit it yourself: steps, appointment reminders, checklist and stage (CMPS271-72/73/77/79)",
    "group_applications": "One account applies for several travelers on the same trip (CMPS271-81)",
}


# Tested features that default on in production as well (Sprint 5 release). FEATURE_<NAME>=false still
# turns one off. The agent waits for a larger host and email copies for SES production access.
RELEASED = frozenset({"passport_scan_quality", "embassy_pdf", "supporting_documents", "privacy_consent", "visa_guide",
                      "malware_scan", "payments", "requirements_editor", "kpi_dashboard",
                      "passport_rules", "photo_check", "document_vault", "document_translation",
                      "support_tools", "generated_documents",
                      "submission_tracking", "group_applications"})


def _variable(name: str) -> str:
    return f"FEATURE_{name.upper()}"


@dataclass(frozen=True)
class FeatureFlags:
    enabled: frozenset[str]

    @classmethod
    def from_environment(cls) -> "FeatureFlags":
        default = os.environ.get("APP_ENV", "development").lower() != "production"
        enabled = set()
        for name in FLAGS:
            raw = os.environ.get(_variable(name), "").strip().lower()
            if raw not in {"", "true", "false"}:
                raise RuntimeError(f"{_variable(name)} must be true or false.")
            if raw == "true" or (raw == "" and (default or name in RELEASED)):
                enabled.add(name)
        return cls(frozenset(enabled))

    def is_on(self, name: str) -> bool:
        if name not in FLAGS:
            raise KeyError(f"Unknown feature flag: {name}")
        return name in self.enabled

    def as_dict(self) -> dict[str, bool]:
        return {name: name in self.enabled for name in FLAGS}


def require_feature(name: str):
    """A route dependency that answers 404 while the flag is off, as if the route did not exist.

    Use it on a router, so every route of the unfinished feature is hidden at once:
    APIRouter(prefix="/documents", dependencies=[Depends(require_feature("supporting_documents"))])
    """
    if name not in FLAGS:
        # Raised when the router is built, so a misspelt flag fails the tests, not a request.
        raise KeyError(f"Unknown feature flag: {name}")

    def check(request: Request) -> None:
        if not request.app.state.feature_flags.is_on(name):
            raise HTTPException(status_code=404, detail="Not Found")

    return check


def create_features_router() -> APIRouter:
    router = APIRouter(tags=["Feature flags"])

    @router.get("/features")
    def features(request: Request, response: Response) -> dict[str, bool]:
        # Flags change when the server restarts with a new environment, so never cache them.
        response.headers["Cache-Control"] = "no-store"
        return request.app.state.feature_flags.as_dict()

    return router
