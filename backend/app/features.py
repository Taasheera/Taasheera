"""Feature flags (US 127): merge unfinished work without showing it on the live site.

A flag is on by default in development and off by default in production, so a merged but
untested feature works locally and stays hidden on the live site. Set FEATURE_<NAME>=true
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
}



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
            if raw == "true" or (raw == "" and default):
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
