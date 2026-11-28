from contextlib import asynccontextmanager
import os
import shutil
from pathlib import Path
from typing import Annotated

import anyio.to_thread
import bcrypt
from filelock import FileLock, Timeout
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from google import genai
from sqlalchemy.exc import IntegrityError
from sqlalchemy import text as sql_text
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, SQLModel, select

from .database import api_threads, build_engine
from .file_encryption import cipher_from_environment
from .applications.form_review import create_form_review_router
from .applications.portal_guide import create_portal_guide_router
from .applications.router import application_context, application_proposal, create_applications_router
from .activity import activity_directory_from_environment, create_activity_router, record_activity
from .admin_api import create_admin_router, install_admin_no_store
from .assistant import DEFAULT_MODEL, create_assistant_router, gemini_ask
from .assistant_tools import TOOLS_PROMPT, gemini_ask_tools
from .chat_history import create_chat_history_router, load_history, save_turn
from .auth import check_origin, create_auth_router, create_current_traveler
from .sessions import create_sessions_router
from .google_auth import create_google_auth_router
from .config import AuthSettings, RuntimeSettings
from .features import FeatureFlags, create_features_router
from .schema import require_current_schema
from .observability import install_private_no_store, install_query_timing, install_request_logging
from .mailer import LogNotificationSender, MailSettings, NotificationSender, ResetSender, SesResetSender, SmtpResetSender
from .notification_jobs import (NotificationJobThread, job_interval, logger as notification_logger,
                                record_passport_expiry, run_notification_jobs, show_logs as show_notification_logs)
from .security import audit, throttle
from .password_reset import create_password_reset_router
from .models import Application, RegisterRequest, Traveler, TravelerPublic
from .applications import flow
from .passport.router import create_passport_router
from .passport.storage import PassportImageStorage
from .profile import create_profile_router
from .consent import create_consent_requirement, create_consent_router
from .notification_preferences import create_notification_preferences_router
from .applications.visa_guides import create_visa_guides_router
from .document_audit import create_document_audit_router, record_document_access
from .load_testing import fake_assistant_from_environment
from .monitoring import configure_monitoring, install_tracing, monitored_job
from .privacy import create_privacy_router, retry_erasures
from .system_health import create_system_health_router
from .notifications import create_notifications_router
from .agent.router import create_agent_router
from .agent.storage import AgentFiles
from .documents.router import create_documents_router
from .generated.router import create_generated_documents_router, generated_packet_files
from .applications.tracking import record_packet_download
from .documents.review import booking_documents as booking_texts, completeness_issues, missing_document_issues
from .documents.storage import DocumentStorage
from .documents.translation import gemini_translate
from .documents.vault import create_vault_router, vault_issues
from .personal_data import create_data_export_router
from .upload_scan import DOCUMENT_TYPES, PASSPORT_TYPES, UploadScanner, create_upload_check
from .payments.router import create_payments_router
from .requirements import RequirementStore, create_requirements_router
from .kpis import create_kpi_router
from .support import create_staff_support_router, create_support_router
from .help_content import create_help_content_admin_router, create_help_content_router


def create_app(database_url: str | None = None, *, reset_sender: ResetSender | None = None,
               notification_sender: NotificationSender | None = None) -> FastAPI:
    engine = build_engine(database_url)
    install_query_timing(engine)
    # Before the engine is used, so database spans are recorded from the first request.
    monitoring = configure_monitoring()
    requirements = RequirementStore(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.auth_settings = AuthSettings.from_environment()
        app.state.runtime_settings = RuntimeSettings.from_environment()
        app.state.feature_flags = FeatureFlags.from_environment()
        app.state.activity_directory = activity_directory_from_environment()
        app.state.account_lock_directory = app.state.activity_directory.parent / ".account-locks"
        app.state.mail_settings = MailSettings.from_environment()
        app.state.reset_sender = reset_sender or (
            (SesResetSender(app.state.mail_settings) if app.state.mail_settings.provider == "ses"
             else SmtpResetSender(app.state.mail_settings)) if app.state.mail_settings else None
        )
        if app.state.runtime_settings.schema_auto_create:
            SQLModel.metadata.create_all(engine)
        else:
            require_current_schema(engine)
        retry_erasures(engine, passport_storage, app.state.activity_directory, app.state.auth_settings.jwt_secret)
        set_api_threads()
        requirements.install()
        notification_jobs = start_notification_jobs(app)
        try:
            yield
        finally:
            requirements.uninstall()
            if notification_jobs is not None:
                notification_jobs.stop()
                notification_jobs.leader.release()
            if monitoring is not None:
                monitoring.flush(timeout=2)
            engine.dispose()

    def start_notification_jobs(app: FastAPI) -> NotificationJobThread | None:
        """Email copies of notifications (CMPS271-98), sent by a timer in this process."""
        if not app.state.feature_flags.is_on("email_notifications"):
            return None
        interval = job_interval()
        sender = notification_sender or app.state.reset_sender
        if not hasattr(sender, "send_notification"):
            # Development without a mail server logs the attempt; production sends none.
            sender = (LogNotificationSender(notification_logger)
                      if app.state.runtime_settings.environment == "development" else None)
        if not interval or sender is None:
            return None
        # With several API worker processes (WEB_CONCURRENCY), exactly one runs the timer: whichever
        # takes this lock on the shared storage volume first. The others serve requests only.
        app.state.account_lock_directory.parent.mkdir(parents=True, exist_ok=True)
        leader = FileLock(str(app.state.account_lock_directory.parent / ".notification-jobs.lock"))
        try:
            leader.acquire(timeout=0)
        except Timeout:
            return None
        # The first allowed origin is the site itself (TAASHEERA_SITE_URL in production).
        site_url = app.state.auth_settings.allowed_origins[0]
        show_notification_logs()

        def run_jobs():
            # A Sentry cron monitor alerts when a run fails or stops arriving.
            with monitored_job("notification-jobs", interval_seconds=interval):
                run_notification_jobs(engine, sender, site_url)

        jobs = NotificationJobThread(run_jobs, interval)
        jobs.leader = leader
        jobs.start()
        return jobs

    def set_api_threads() -> None:
        """Match the endpoint thread pool to API_THREADS, the size build_engine gave the SQLite pool."""
        anyio.to_thread.current_default_thread_limiter().total_tokens = api_threads()

    app = FastAPI(title="Taasheera traveler API", lifespan=lifespan)
    app.state.engine = engine
    install_request_logging(app)
    install_admin_no_store(app)
    install_private_no_store(app)
    install_tracing(app, monitoring)

    @app.get("/health/live", include_in_schema=False)
    def liveness():
        return {"status": "ok"}

    @app.get("/health/ready", include_in_schema=False)
    def readiness(request: Request):
        try:
            with engine.connect() as connection:
                connection.execute(sql_text("SELECT 1"))
            if not request.app.state.runtime_settings.schema_auto_create:
                require_current_schema(engine)
            if request.app.state.runtime_settings.require_tesseract and shutil.which("tesseract") is None:
                raise RuntimeError("OCR unavailable")
        except (SQLAlchemyError, RuntimeError):
            raise HTTPException(status_code=503, detail="Service is not ready.") from None
        return {"status": "ok"}

    def get_session():
        with Session(engine) as session:
            yield session

    current_traveler = create_current_traveler(get_session)
    passport_storage = PassportImageStorage(
        os.environ.get("PASSPORT_STORAGE_DIR", str(Path(__file__).resolve().parents[1] / "storage" / "passports")),
        on_access=lambda owner, upload, purpose: record_document_access(
            engine, app.state.activity_directory, owner, upload, purpose),
        cipher=cipher_from_environment(),
    )
    app.state.passport_storage = passport_storage
    app.include_router(create_features_router())
    app.include_router(create_activity_router(get_session, current_traveler))
    app.include_router(create_document_audit_router(get_session, current_traveler))
    app.include_router(create_privacy_router(get_session, current_traveler, passport_storage))
    app.include_router(create_data_export_router(get_session, current_traveler, passport_storage))
    app.include_router(create_auth_router(get_session, current_traveler))
    app.include_router(create_admin_router(get_session, current_traveler, passport_storage))
    app.include_router(create_system_health_router(get_session, current_traveler, passport_storage.root))
    app.include_router(create_requirements_router(get_session, current_traveler, requirements))
    app.include_router(create_kpi_router(get_session, current_traveler))
    app.include_router(create_sessions_router(get_session, current_traveler))
    app.include_router(create_google_auth_router(get_session, current_traveler))
    app.include_router(create_password_reset_router(get_session))
    app.include_router(create_profile_router(get_session, current_traveler))
    app.include_router(create_consent_router(get_session, current_traveler))
    app.include_router(create_notification_preferences_router(get_session, current_traveler))
    app.include_router(create_visa_guides_router())
    # No identity document is accepted before the privacy policy is (CMPS271-115).
    privacy_consent = Depends(create_consent_requirement(get_session, current_traveler))
    app.include_router(create_notifications_router(get_session, current_traveler))

    def current_traveler_id(traveler: Annotated[Traveler, Depends(current_traveler)]) -> int:
        return traveler.id

    def record_passport_activity(traveler_id: int, event_type: str, outcome: str):
        with Session(engine) as session:
            record_activity(session, app.state.activity_directory, traveler_id,
                            event_type, "passport", outcome)

    uploads_setting = os.environ.get("PASSPORT_UPLOADS_ENABLED", "true").lower()
    if uploads_setting not in {"true", "false"}:
        raise RuntimeError("PASSPORT_UPLOADS_ENABLED must be true or false.")

    def record_upload_rejection(traveler_id: int, identifier: str, reason: str):
        with Session(engine) as session:
            record_activity(session, app.state.activity_directory, traveler_id, "upload_rejected", identifier, reason)

    upload_scanner = UploadScanner.from_environment()

    def scan_check(source: str, allowed: frozenset[str]):
        return create_upload_check(upload_scanner, allowed, source=source,
                                   enabled=lambda: app.state.feature_flags.is_on("malware_scan"),
                                   record_rejection=record_upload_rejection)

    def remember_passport_expiry(traveler_id: int, fields) -> None:
        # Only the expiry date, for reminders (CMPS271-36); kept while email notifications are on.
        if app.state.feature_flags.is_on("email_notifications"):
            record_passport_expiry(engine, traveler_id, fields.expiry_date)

    app.include_router(create_passport_router(
        passport_storage, current_traveler_id, record_event=record_passport_activity,
        uploads_enabled=uploads_setting == "true",
        check_quality=lambda: app.state.feature_flags.is_on("passport_scan_quality"),
        upload_dependencies=[privacy_consent],
        on_confirmed=remember_passport_expiry,
        scan_upload=scan_check("passport", PASSPORT_TYPES),
    ))

    # Supporting documents share the passport folder, so account erasure removes them too (CMPS271-29).
    document_storage = DocumentStorage(passport_storage.root, on_access=passport_storage.on_access,
                                       cipher=passport_storage.cipher)
    # The agent's screenshots show passport details, so they live there too (CMPS271-70).
    agent_files = AgentFiles(passport_storage.root, on_access=passport_storage.on_access, cipher=passport_storage.cipher)

    def remove_document_files(owner: int, document_ids: list[str]):
        for document_id in document_ids:
            document_storage.delete(owner, document_id)

    def document_issues(session, application, answers):
        # Until the documents feature is released, the checks stay exactly as they were.
        if not app.state.feature_flags.is_on("supporting_documents"):
            return None
        from .applications.router import saved_profile
        flags = app.state.feature_flags
        return completeness_issues(session, document_storage, application, answers,
                                   latest_passport=passport_storage.latest_review(application.traveler_id, purpose="consistency_check"),
                                   profile=saved_profile(session, application.traveler_id)) + vault_issues(
            session, document_storage, application, answers,
            photos=flags.is_on("photo_check"), passport=flags.is_on("passport_rules"))

    app.state.document_issues = document_issues

    def booking_documents(session, application):
        # Bookings suggest answers only once supporting documents are released.
        if not app.state.feature_flags.is_on("supporting_documents"):
            return None
        return booking_texts(session, document_storage, application)

    app.include_router(create_applications_router(get_session, current_traveler,
        lambda owner, upload: passport_storage.load_review(owner, upload, purpose="application_prefill"),
        load_latest_passport=lambda owner: passport_storage.latest_review(owner, purpose="application_prefill"),
        remove_document_files=remove_document_files, document_issues=document_issues,
        remove_agent_files=agent_files.remove_application, booking_documents=booking_documents))
    app.include_router(create_agent_router(get_session, current_traveler, agent_files))
    app.include_router(create_form_review_router(get_session, current_traveler,
        lambda owner: passport_storage.latest_review(owner, purpose="consistency_check"), document_issues))
    app.include_router(create_portal_guide_router(get_session, current_traveler))
    app.include_router(create_payments_router(get_session, current_traveler))

    def missing_uploads(session, application, answers):
        if not app.state.feature_flags.is_on("supporting_documents"):
            return None
        return missing_document_issues(session, application, answers)

    app.include_router(create_support_router(get_session, current_traveler))
    app.include_router(create_staff_support_router(get_session, current_traveler, document_issues=missing_uploads))
    app.include_router(create_help_content_router(get_session))
    app.include_router(create_help_content_admin_router(get_session, current_traveler))

    def packet_generated_files(session, application, questionnaire, answers, email):
        if not app.state.feature_flags.is_on("generated_documents"):
            return {}
        return generated_packet_files(session, application, questionnaire, answers, email)

    def record_document_activity(traveler_id: int, event_type: str, requirement: str):
        with Session(engine) as session:
            record_activity(session, app.state.activity_directory, traveler_id, event_type, requirement, "success")

    app.include_router(create_documents_router(get_session, current_traveler, document_storage,
                                               record_event=record_document_activity,
                                               load_latest_passport=lambda owner: passport_storage.latest_review(owner, purpose="consistency_check"),
                                               upload_dependencies=[privacy_consent],
                                               scan_upload=scan_check("document", DOCUMENT_TYPES),
                                               generated_files=packet_generated_files,
                                               on_packet_download=record_packet_download))
    app.include_router(create_generated_documents_router(get_session, current_traveler))

    def record_assistant_activity(traveler_id: int, event_type: str, outcome: str):
        with Session(engine) as session:
            record_activity(session, app.state.activity_directory, traveler_id,
                            event_type, "assistant", outcome)

    # Without a key the assistant answers 503 and the rest of the app works as normal.
    assistant_key = os.environ.get("GEMINI_API_KEY", "").strip()
    # 20 s per attempt, 2 attempts at most: the browser gives up after 45 s.
    assistant_client = genai.Client(
        api_key=assistant_key, http_options={"timeout": 20_000, "retry_options": {"attempts": 2}},
    ) if assistant_key else None
    assistant_model = os.environ.get("ASSISTANT_MODEL", DEFAULT_MODEL)
    assistant_ask = gemini_ask(assistant_client, assistant_model) if assistant_client else None
    assistant_ask_tools = gemini_ask_tools(assistant_client, assistant_model) if assistant_client else None
    # Staging capacity tests only: a fixed-latency stand-in instead of Gemini (app/load_testing.py).
    stand_in = fake_assistant_from_environment()
    if stand_in is not None:
        assistant_ask, assistant_ask_tools = stand_in
    app.include_router(create_vault_router(get_session, current_traveler, document_storage,
                                           record_event=record_document_activity,
                                           translate=gemini_translate(assistant_client, assistant_model) if assistant_client else None))

    def load_application_context(traveler_id: int, application_id: str) -> str | None:
        with Session(engine) as session:
            return application_context(session, traveler_id, application_id,
                                       latest_passport=passport_storage.latest_review(traveler_id),
                                       document_issues=document_issues)

    def draft_context(traveler_id: int, application_id: str, question_id: str):
        # Only a reachable free-text question long enough to need help, in an application that can change.
        context = load_application_context(traveler_id, application_id)
        if context is None:
            return None
        with Session(engine) as session:
            application = session.get(Application, application_id)
            if application is None or application.archived_at is not None:
                return None
            question = flow.load_questionnaire(application.questionnaire_id).question(question_id)
        if question is None or question.type != "text" or (question.max_length or 0) < 150:
            return None
        return context, question.prompt, question.max_length

    def stored_history(traveler_id: int, application_id: str | None) -> list[dict]:
        with Session(engine) as session:
            return load_history(session, traveler_id, application_id)

    def store_turn(traveler_id: int, application_id: str | None, question: str, reply: str):
        with Session(engine) as session:
            save_turn(session, traveler_id, application_id, question, reply)

    def propose_action(traveler_id: int, application_id: str, name: str, args: dict):
        with Session(engine) as session:
            return application_proposal(session, traveler_id, application_id, name, args,
                                        latest_passport=passport_storage.latest_review(traveler_id),
                                        document_issues=document_issues)

    app.include_router(create_assistant_router(
        get_session, current_traveler_id, passport_storage.latest_review, assistant_ask,
        record_event=record_assistant_activity, load_application=load_application_context,
        ask_tools=assistant_ask_tools, propose=propose_action, tools_prompt=TOOLS_PROMPT,
        load_history=stored_history, save_turn=store_turn, load_draft_context=draft_context,
    ))
    app.include_router(create_chat_history_router(get_session, current_traveler_id))

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        # FastAPI's default errors can include input values, including passwords.
        errors = [
            {key: error[key] for key in ("loc", "msg", "type")}
            for error in exc.errors()
        ]
        return JSONResponse(status_code=422, content={"detail": errors})

    @app.exception_handler(Exception)
    async def service_error_handler(request: Request, exc: Exception):
        return JSONResponse(status_code=500, content={"detail": "Service unavailable. Please try again."},
            headers={"Cache-Control": "no-store", "X-Request-ID": getattr(request.state, "request_id", "unavailable")})

    @app.post(
        "/auth/register",
        response_model=TravelerPublic,
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(check_origin)],
    )
    def register(
        data: RegisterRequest,
        request: Request,
        session: Annotated[Session, Depends(get_session)],
    ) -> Traveler:
        throttle(request, session, "register", account=data.email, ip_limit=10, account_limit=3)
        email_query = select(Traveler).where(Traveler.email == data.email)
        if session.exec(email_query).first() is not None:
            audit(request, "register", "conflict")
            # No authenticated traveler owns this attempted registration.
            raise HTTPException(status_code=409, detail="Email is already registered.")

        password_hash = bcrypt.hashpw(
            data.password.get_secret_value().encode("utf-8"),
            bcrypt.gensalt(rounds=12),
        ).decode("ascii")
        traveler = Traveler(name=data.name, email=data.email, password_hash=password_hash)
        session.add(traveler)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            # The unique constraint also catches simultaneous registrations.
            if session.exec(email_query).first() is not None:
                raise HTTPException(status_code=409, detail="Email is already registered.") from None
            raise

        session.refresh(traveler)
        audit(request, "register", "success", traveler.id)
        record_activity(session, app.state.activity_directory, traveler.id,
                        "registration", "email_password", "success")
        return traveler

    return app


app = create_app()
