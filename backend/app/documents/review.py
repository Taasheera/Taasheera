"""Put the uploaded documents through the cross-check and the completeness check (CMPS271-50).

Shared by the documents page, the completeness check (US 13) and the assistant's view of an
application, so all three report the same thing.
"""

from datetime import date

from sqlmodel import Session, select

from ..applications import completeness, flow
from ..models import Application, SupportingDocument
from .booking_dates import BOOKED_FROM, booking_findings
from .crosscheck import DocumentText, Report, cross_check
from .storage import DocumentStorage


def _documents(session: Session, application: Application) -> list[SupportingDocument]:
    return list(session.exec(select(SupportingDocument)
                             .where(SupportingDocument.application_id == application.id)
                             .order_by(SupportingDocument.uploaded_at, SupportingDocument.id)).all())


def document_cross_check(session: Session, storage: DocumentStorage, application: Application,
                         answers: flow.Answers, *, latest_passport: dict | None, profile: dict | None) -> Report:
    questionnaire = flow.load_questionnaire(application.questionnaire_id)
    labels = {document.id: document.label for document in questionnaire.documents}
    skipped = {document.id for document in questionnaire.documents if not document.crosscheck}
    texts = [DocumentText(id=document.id, requirement=document.requirement,
                          label=labels.get(document.requirement, "Document"), filename=document.filename,
                          text=storage.load_text(application.traveler_id, document.file_key, purpose="document_check"))
             for document in _documents(session, application) if document.requirement not in skipped]
    passport = answers.get("passport") if isinstance(answers.get("passport"), dict) else None
    report = cross_check(passport, latest_passport=latest_passport, profile=profile, documents=texts)
    arrival, departure = answers.get("arrival_date"), answers.get("departure_date")
    if arrival and departure:
        for document in texts:
            report.findings.extend(booking_findings(document, date.fromisoformat(arrival), date.fromisoformat(departure)))
    return report


def booking_documents(session: Session, storage: DocumentStorage, application: Application) -> list[DocumentText]:
    """The text of the bookings that can suggest answers: flights, itineraries and accommodation."""
    questionnaire = flow.load_questionnaire(application.questionnaire_id)
    labels = {document.id: document.label for document in questionnaire.documents}
    wanted = {requirement for requirements in BOOKED_FROM.values() for requirement in requirements}
    return [DocumentText(id=document.id, requirement=document.requirement,
                         label=labels.get(document.requirement, "Document"), filename=document.filename,
                         text=storage.load_text(application.traveler_id, document.file_key, purpose="document_prefill"))
            for document in _documents(session, application) if document.requirement in wanted]


def completeness_issues(session: Session, storage: DocumentStorage, application: Application, answers: flow.Answers,
                        *, latest_passport: dict | None, profile: dict | None) -> list[completeness.Issue]:
    """Required documents not uploaded yet, and documents that disagree with the passport.

    Passport-versus-profile differences are left out: the completeness check reports those itself.
    """
    questionnaire = flow.load_questionnaire(application.questionnaire_id)
    uploaded = {document.requirement for document in _documents(session, application)}
    issues = [completeness.Issue(kind="missing", severity="blocking", message=f"Not uploaded yet: {requirement.label}",
                                 document_id=requirement.id)
              for requirement in flow.required_documents(questionnaire, answers)
              if requirement.required and requirement.id not in uploaded]
    report = document_cross_check(session, storage, application, answers,
                                  latest_passport=latest_passport, profile=profile)
    issues += [completeness.Issue(kind="inconsistent", severity="warning", message=finding.message,
                                  document_id=finding.requirement)
               for finding in report.findings if finding.source == "document"]
    return issues
