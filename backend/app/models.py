from datetime import date, datetime, timezone
from typing import Annotated, Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, EmailStr, SecretStr, StringConstraints, field_validator
from pydantic import Field as SchemaField
from sqlmodel import Field, SQLModel
from sqlalchemy import JSON, CheckConstraint, Column, Text, UniqueConstraint


# Stored as plain strings guarded by named CHECK constraints (portable; no database enum types).
Role = Literal["traveler", "support", "admin"]
Status = Literal["active", "suspended"]
ROLES: tuple[str, ...] = get_args(Role)
STATUSES: tuple[str, ...] = get_args(Status)


class Traveler(SQLModel, table=True):
    __table_args__ = (
        CheckConstraint(
            "(auth_type = 'google' AND password_hash = '') OR "
            "(auth_type = 'email_password' AND password_hash <> '')",
            name="ck_traveler_auth_type",
        ),
        CheckConstraint("role IN ('traveler', 'support', 'admin')", name="ck_traveler_role"),
        CheckConstraint("status IN ('active', 'suspended')", name="ck_traveler_status"),
    )
    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(max_length=100)
    email: str = Field(max_length=254, unique=True, index=True)
    # Latest verified Google email; identities authenticate by Google subject.
    sso_email: str | None = Field(default=None, max_length=254, index=True)
    # Empty means password authentication is disabled (Google-only account).
    # Preserve the existing NOT NULL column for compatibility with local DBs.
    password_hash: str = Field(max_length=60, repr=False)
    auth_type: str = Field(default="email_password", max_length=20, index=True)
    last_login_method: str | None = Field(default=None, max_length=20)
    last_login_at: int | None = None
    # Never taken from a request body: sign-up always creates an active traveler.
    # Only manage.py set-role (and, later, admins) change these.
    role: str = Field(default="traveler", max_length=20, sa_column_kwargs={"server_default": "traveler"})
    status: str = Field(default="active", max_length=20, sa_column_kwargs={"server_default": "active"})
    # Sign-up time; None for accounts made before it was recorded.
    created_at: datetime | None = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def password_enabled(self) -> bool:
        return self.auth_type == "email_password" and bool(self.password_hash)


class TravelerProfile(SQLModel, table=True):
    traveler_id: int = Field(primary_key=True, foreign_key="traveler.id")
    surname: str | None = Field(default=None, max_length=100, repr=False)
    given_names: str | None = Field(default=None, max_length=100, repr=False)
    birth_date: date | None = Field(default=None, repr=False)
    nationality: str | None = Field(default=None, max_length=3, repr=False)
    phone: str | None = Field(default=None, max_length=16, repr=False)
    address_line1: str | None = Field(default=None, sa_type=Text, repr=False)
    address_line2: str | None = Field(default=None, sa_type=Text, repr=False)
    address_city: str | None = Field(default=None, sa_type=Text, repr=False)
    address_region: str | None = Field(default=None, sa_type=Text, repr=False)
    address_postal_code: str | None = Field(default=None, sa_type=Text, repr=False)
    address_country: str | None = Field(default=None, max_length=3, repr=False)


class PrivacyConsent(SQLModel, table=True):
    """One privacy policy version a traveler accepted, and when (CMPS271-115).

    A new policy version needs a new acceptance, so earlier rows stay as the record of what was agreed.
    """

    __table_args__ = (UniqueConstraint("traveler_id", "policy_version", name="uq_privacyconsent_traveler_version"),)
    id: int | None = Field(default=None, primary_key=True)
    traveler_id: int = Field(foreign_key="traveler.id", index=True)
    policy_version: str = Field(max_length=20)
    accepted_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Application(SQLModel, table=True):
    # Random hex ids, like passport uploads, so ids can't be guessed or counted.
    id: str = Field(primary_key=True, max_length=32)
    traveler_id: int = Field(foreign_key="traveler.id", index=True)
    # Which question file (and version) the answers belong to.
    questionnaire_id: str = Field(max_length=60)
    questionnaire_version: int
    destination: str = Field(max_length=20)
    visa_type: str = Field(max_length=40)
    # How many people travel together, counting the traveler (US 4). Recorded only, for now:
    # each application is for one person until group applications (US 81).
    group_size: int = Field(default=1, sa_column_kwargs={"server_default": "1"})
    # DRAFT while answering, INELIGIBLE when an answer stops the flow, ANSWERS_COMPLETE when nothing is left.
    # Then what the traveler records themself (US 55): SUBMITTED, and APPROVED or REFUSED. The agent never submits.
    status: str = Field(default="DRAFT", max_length=20)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    # Set when the traveler archives it (US 79): hidden from the dashboard, kept until they delete it.
    archived_at: datetime | None = None
    # When the traveler recorded handing it to the embassy, and the embassy's decision (US 55).
    submitted_at: datetime | None = None
    decided_at: datetime | None = None


class ApplicationAnswer(SQLModel, table=True):
    """One answer, saved as soon as it is given. A copy, so later passport or profile edits don't change it."""

    application_id: str = Field(primary_key=True, foreign_key="application.id", max_length=32)
    question_id: str = Field(primary_key=True, max_length=40)
    value: Any = Field(sa_column=Column(JSON, nullable=False), repr=False)
    # "traveler" (typed or chosen in the chat), "passport_upload" (a passport confirmed for this
    # application), or "passport" / "profile" (a saved passport or profile the traveler confirmed here).
    source: str = Field(max_length=20)
    answered_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ActivityEvent(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    traveler_id: int = Field(foreign_key="traveler.id", index=True)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), index=True)
    event_type: str = Field(max_length=40, index=True)
    identifier: str = Field(max_length=80)
    outcome: str = Field(max_length=20)


class GoogleIdentity(SQLModel, table=True):
    subject: str = Field(primary_key=True, max_length=255)
    traveler_id: int = Field(foreign_key="traveler.id", unique=True, index=True)


class GoogleLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id_token: SecretStr = SchemaField(min_length=1, max_length=8192)


class PasswordInput(BaseModel):
    password: SecretStr = SchemaField(min_length=8, max_length=72)

    @field_validator("password")
    @classmethod
    def validate_password_bytes(cls, value: SecretStr) -> SecretStr:
        try:
            encoded = value.get_secret_value().encode("utf-8")
        except UnicodeError:
            raise ValueError("Password must contain valid Unicode characters") from None
        if len(encoded) > 72:
            raise ValueError("Password must be at most 72 UTF-8 bytes")
        return value


class RegisterRequest(PasswordInput):
    # This endpoint only creates travelers; callers cannot supply roles or IDs.
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    email: EmailStr = SchemaField(max_length=254)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.lower()


class GoogleLinkLoginRequest(PasswordInput):
    model_config = ConfigDict(extra="forbid")
    id_token: SecretStr = SchemaField(min_length=1, max_length=8192)


class GoogleLinkRequest(GoogleLinkLoginRequest):
    expected_traveler_id: int = SchemaField(gt=0)


class ChangePasswordRequest(PasswordInput):
    model_config = ConfigDict(extra="forbid")
    current_password: SecretStr = SchemaField(min_length=1, max_length=72)
    # Inherit PasswordInput's length and UTF-8 byte validation for the new password.
    password: SecretStr = SchemaField(alias="new_password", min_length=8, max_length=72)


class AuthConfigurationPublic(BaseModel):
    google_client_id: str | None


class TravelerPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    email: str
    sso_email: str | None = None
    auth_type: Literal["email_password", "google"] = "email_password"
    password_enabled: bool = True
    last_login_method: str | None = None


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: SecretStr

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.lower()


class AccessTokenPublic(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int


class AuthSession(SQLModel, table=True):
    id: str = Field(primary_key=True)
    traveler_id: int = Field(foreign_key="traveler.id", index=True)
    refresh_hash: str = Field(max_length=64, repr=False)
    expires_at: int
    revoked: bool = False
    device_label: str | None = Field(default=None, max_length=100)
    created_at: datetime | None = None
    last_active_at: datetime | None = None


class RefreshToken(SQLModel, table=True):
    # Retain past hashes until session cleanup so replay revokes the whole session.
    token_hash: str = Field(primary_key=True, max_length=64, repr=False)
    session_id: str = Field(foreign_key="authsession.id", index=True)


class PasswordResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr = SchemaField(max_length=254)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.lower()


class PasswordResetConfirm(PasswordInput):
    model_config = ConfigDict(extra="forbid")
    token: SecretStr = SchemaField(min_length=1, max_length=128)


class PasswordResetVerify(PasswordResetRequest):
    code: SecretStr = SchemaField(min_length=6, max_length=6)

    @field_validator("code")
    @classmethod
    def ascii_digits(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().isascii() or not value.get_secret_value().isdigit():
            raise ValueError("Enter the six-digit code from your email.")
        return value


class PasswordResetGrant(BaseModel):
    reset_token: str
    expires_in: int


class PasswordResetChallenge(SQLModel, table=True):
    traveler_id: int = Field(primary_key=True, foreign_key="traveler.id")
    nonce: str = Field(max_length=64, repr=False)
    code_digest: str = Field(max_length=64, repr=False)
    expires_at: int = Field(index=True)
    attempts: int = 0
    consumed: bool = False


class PasswordResetToken(SQLModel, table=True):
    token_hash: str = Field(primary_key=True, max_length=64, repr=False)
    traveler_id: int = Field(foreign_key="traveler.id", index=True)
    expires_at: int = Field(index=True)
    used: bool = False


class ResetRateLimit(SQLModel, table=True):
    # Scope + SHA-256 of the normalized email or client IP; no raw identifiers.
    key: str = Field(primary_key=True, max_length=100)
    expires_at: int = Field(index=True)
    count: int = 1


class AssistantMessage(SQLModel, table=True):
    """One turn of a traveler's chat with the assistant (US 111), so it reloads on the next visit.

    Kept per traveler and per application (none for chats outside an application). Holds what the
    traveler typed, which may mention passport details, so reads are owner-scoped like the rest.
    """

    id: int | None = Field(default=None, primary_key=True)
    traveler_id: int = Field(foreign_key="traveler.id", index=True)
    application_id: str | None = Field(default=None, foreign_key="application.id", max_length=32, index=True)
    role: str = Field(max_length=10)  # "user" or "assistant"
    content: str = Field(sa_type=Text, repr=False)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Notification(SQLModel, table=True):
    """An in-app message for one traveler (US 28), listed in the notification centre (US 107).

    Plain sentences about their own applications: never passport fields or answer values.
    """

    id: int | None = Field(default=None, primary_key=True)
    traveler_id: int = Field(foreign_key="traveler.id", index=True)
    application_id: str | None = Field(default=None, foreign_key="application.id", max_length=32, index=True)
    kind: str = Field(max_length=30)  # "status_changed", "agent_finished" or "agent_stopped"
    message: str = Field(max_length=300)
    link: str | None = Field(default=None, max_length=200)  # A page on this site, such as /dashboard/<id>
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), index=True)
    read_at: datetime | None = None


class SupportingDocument(SQLModel, table=True):
    """A file uploaded for one of an application's document requirements (CMPS271-29).

    The file sits in the traveler's private storage folder; this row says what it is for.
    The display name is the traveler's own file name, cleaned, and is never used in a path.
    """

    id: str = Field(primary_key=True, max_length=32)
    traveler_id: int = Field(foreign_key="traveler.id", index=True)
    application_id: str = Field(foreign_key="application.id", max_length=32, index=True)
    requirement: str = Field(max_length=40)
    filename: str = Field(max_length=120, repr=False)
    content_type: str = Field(max_length=40)
    size_bytes: int
    uploaded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AgentRun(SQLModel, table=True):
    """One attempt by the form-filling agent at an embassy's online form (CMPS271-236, CMPS271-67).

    The API queues it when the traveler asks; the separate worker process claims it, fills the form
    in a headless browser and stops before submitting. Status: queued, running, ready (filled and
    waiting for the traveler), stopped (CAPTCHA, unknown field, site problem), failed or cancelled.
    """

    id: str = Field(primary_key=True, max_length=32)
    traveler_id: int = Field(foreign_key="traveler.id", index=True)
    application_id: str = Field(foreign_key="application.id", max_length=32, index=True)
    site: str = Field(max_length=40)
    status: str = Field(default="queued", max_length=20, index=True)
    # A fixed explanation for the traveler, never a value typed into the form.
    message: str | None = Field(default=None, max_length=300)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = None
    finished_at: datetime | None = None


class AgentFieldEntry(SQLModel, table=True):
    """One field the agent typed (CMPS271-70, US 75): what, where and when, so the traveler can check it."""

    id: int | None = Field(default=None, primary_key=True)
    run_id: str = Field(foreign_key="agentrun.id", max_length=32, index=True)
    page: int
    page_title: str = Field(max_length=100)
    field: str = Field(max_length=120)
    value: str = Field(sa_type=Text, repr=False)
    entered_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AgentScreenshot(SQLModel, table=True):
    """A screenshot of one form page after the agent filled it; the file is in the owner's private folder."""

    id: str = Field(primary_key=True, max_length=32)
    run_id: str = Field(foreign_key="agentrun.id", max_length=32, index=True)
    page: int
    page_title: str = Field(max_length=100)
    taken_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# Recorded by the traveler (US 55). Answers are frozen from then on: they are what the embassy received.
RECORDED_STATUSES = ("SUBMITTED", "APPROVED", "REFUSED")
APPLICATION_STATUSES = ("DRAFT", "INELIGIBLE", "ANSWERS_COMPLETE", *RECORDED_STATUSES)


class DocumentAccess(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    traveler_id: int = Field(foreign_key="traveler.id", index=True)
    actor_id: int = Field(foreign_key="traveler.id", index=True)
    upload_id: str = Field(max_length=32)
    purpose: str = Field(max_length=30)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), index=True)


class AccountErasure(SQLModel, table=True):
    # Durable work item: failed file/database cleanup is retried, never reported as complete.
    traveler_id: int = Field(primary_key=True, foreign_key="traveler.id")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
