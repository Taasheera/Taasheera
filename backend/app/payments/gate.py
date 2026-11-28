"""Payment unlocks submission (CMPS271-108): the agent fills the embassy form only once the application is paid for.

Answering, checks, documents, the official form PDF and the print-ready packet stay free. While the
`payments` flag is off, nothing is gated.
"""

from sqlmodel import Session, select

from ..models import Application, Payment

PAYMENT_REQUIRED = ("Pay for this application to let the agent fill the embassy form. "
                    "Everything before this step is free.")


def is_paid(session: Session, application_id: str) -> bool:
    return session.exec(select(Payment.id).where(Payment.application_id == application_id,
                                                 Payment.status == "paid").limit(1)).first() is not None


def payment_due(flags, session: Session, application: Application) -> bool:
    return flags.is_on("payments") and not is_paid(session, application.id)
