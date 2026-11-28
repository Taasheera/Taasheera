"""The price, the (simulated) payment and its receipt for one application (CMPS271-106, 108, 109).

Under /applications/{application_id}/payments, owner-checked like every application route and behind
the `payments` flag. The server works out every amount; the browser only sends a per-checkout
idempotency key, so pressing Pay twice records one payment. Payment is offered once the agent could
fill the application's embassy form, and one paid payment covers every run for that application.
"""

from datetime import datetime, timezone
from typing import Annotated
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from ..activity import record_activity
from ..agent.router import blocker
from ..agent.sites import site_for
from ..features import require_feature
from ..models import Application, Payment, Traveler
from ..notifications import application_name, notify_payment
from .provider import PaymentFailed, PaymentProvider, SimulatedProvider
from .quote import Quote, QuoteLine, build_quote
from .receipt import build_receipt, receipt_number

NOT_AVAILABLE = "Payment opens once the agent can fill this application's embassy form."
FAILED = "The payment didn't go through and nothing was charged. Please try again."


class PaymentPublic(BaseModel):
    id: str
    receipt_number: str
    status: str
    amount_minor: int
    currency: str
    line_items: list[QuoteLine]
    provider: str
    created_at: datetime
    paid_at: datetime | None


class PaymentState(BaseModel):
    quote: Quote
    simulated: bool
    available: bool
    can_pay: bool
    reason: str | None
    payments: list[PaymentPublic]


class PayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9-]{16,64}$")


def _utc(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


def _public(payment: Payment) -> PaymentPublic:
    return PaymentPublic(id=payment.id, receipt_number=receipt_number(payment), status=payment.status,
                         amount_minor=payment.amount_minor, currency=payment.currency,
                         line_items=[QuoteLine(**line) for line in payment.line_items], provider=payment.provider,
                         created_at=_utc(payment.created_at), paid_at=_utc(payment.paid_at))


def create_payments_router(get_session, current_traveler, provider: PaymentProvider | None = None) -> APIRouter:
    from ..auth import check_origin

    provider = provider or SimulatedProvider()
    router = APIRouter(prefix="/applications/{application_id}/payments", tags=["Payments"],
                       dependencies=[Depends(require_feature("payments"))])
    SessionDep = Annotated[Session, Depends(get_session)]
    TravelerDep = Annotated[Traveler, Depends(current_traveler)]

    def owned(session: Session, traveler: Traveler, application_id: str) -> Application:
        application = session.get(Application, application_id)
        if application is None or application.traveler_id != traveler.id:
            raise HTTPException(status_code=404, detail="Application not found.")
        return application

    def paid(session: Session, application: Application) -> list[Payment]:
        return list(session.exec(select(Payment).where(Payment.application_id == application.id,
                                                       Payment.status == "paid")
                                 .order_by(Payment.paid_at.desc(), Payment.id)).all())

    def available(request: Request, application: Application) -> bool:
        return (request.app.state.feature_flags.is_on("agent_form_filling")
                and site_for(application.questionnaire_id) is not None)

    def pay_blocker(request: Request, session: Session, application: Application) -> str | None:
        return blocker(session, application) if available(request, application) else NOT_AVAILABLE

    @router.get("", response_model=PaymentState)
    def state(application_id: str, traveler: TravelerDep, session: SessionDep, request: Request, response: Response):
        """The price breakdown, whether the traveler can pay now, and what they have paid."""
        application = owned(session, traveler, application_id)
        payments = paid(session, application)
        reason = None if payments else pay_blocker(request, session, application)
        response.headers["Cache-Control"] = "private, no-store"
        return PaymentState(quote=build_quote(application), simulated=provider.name == "simulated",
                            available=available(request, application), can_pay=not payments and reason is None,
                            reason=reason, payments=[_public(payment) for payment in payments])

    @router.post("", response_model=PaymentPublic, status_code=201, dependencies=[Depends(check_origin)])
    def pay(application_id: str, data: PayRequest, traveler: TravelerDep, session: SessionDep, request: Request,
            response: Response):
        """Pay the server's quote. Repeating a key, or paying an application already paid for, changes nothing."""
        application = owned(session, traveler, application_id)
        response.headers["Cache-Control"] = "private, no-store"
        session.execute(update(Application).where(Application.id == application.id).values(status=Application.status))
        session.refresh(application)
        existing = session.exec(select(Payment).where(
            Payment.application_id == application.id,
            or_(Payment.status == "paid", Payment.idempotency_key == data.idempotency_key),
        ).order_by((Payment.status == "paid").desc(), Payment.created_at.desc())).first()
        if existing is not None:
            response.status_code = 200
            return _public(existing)
        reason = pay_blocker(request, session, application)
        if reason:
            raise HTTPException(status_code=409, detail=reason)
        quote = build_quote(application)
        payment = Payment(id=uuid.uuid4().hex, traveler_id=traveler.id, application_id=application.id,
                          amount_minor=quote.total_minor, currency=quote.currency,
                          line_items=[line.model_dump() for line in quote.lines], provider=provider.name,
                          idempotency_key=data.idempotency_key)
        session.add(payment)
        try:
            payment.provider_reference = provider.charge(payment)
        except PaymentFailed:
            payment.status = "failed"
            session.commit()
            raise HTTPException(status_code=402, detail=FAILED) from None
        payment.status, payment.paid_at = "paid", datetime.now(timezone.utc)
        notify_payment(session, application)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            existing = session.exec(select(Payment).where(Payment.application_id == application.id,
                                                          Payment.idempotency_key == data.idempotency_key)).first()
            if existing is None:
                raise
            response.status_code = 200
            return _public(existing)
        session.refresh(payment)
        return _public(payment)

    @router.get("/{payment_id}/receipt", response_class=Response,
                responses={200: {"content": {"application/pdf": {}}, "description": "The receipt as a PDF."}})
    def receipt(application_id: str, payment_id: str, traveler: TravelerDep, session: SessionDep, request: Request):
        """The receipt for a paid payment (CMPS271-109). Audited before the file leaves, like the exported record."""
        application = owned(session, traveler, application_id)
        payment = session.get(Payment, payment_id)
        if (payment is None or payment.application_id != application.id or payment.traveler_id != traveler.id
                or payment.status != "paid"):
            raise HTTPException(status_code=404, detail="Receipt not found.")
        content = build_receipt(payment, application_name(application), traveler.name)
        number = receipt_number(payment)
        if record_activity(session, request.app.state.activity_directory, traveler.id,
                           "receipt_download", "payment", "success") is None:
            raise HTTPException(status_code=401, detail="Your account is no longer available.")
        return Response(content, media_type="application/pdf", headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": f'attachment; filename="taasheera-receipt-{number}.pdf"',
            "Content-Security-Policy": "sandbox; default-src 'none'",
            "X-Content-Type-Options": "nosniff",
        })

    return router
