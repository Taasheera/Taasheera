"""Payment numbers for the operator health dashboard: no amounts, travelers or applications."""

from datetime import datetime, timedelta, timezone

from sqlmodel import Session, func, select

from ..models import PAYMENT_STATUSES, Payment


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def payment_health(session: Session, *, now: datetime | None = None) -> dict:
    """Payments started in the last 24 hours by status, and how long the oldest pending one has waited.

    {"last_24_hours": {"pending": 0, "paid": 3, "failed": 0}, "oldest_pending_seconds": None}
    """
    now = now or datetime.now(timezone.utc)
    rows = session.exec(select(Payment.status, func.count()).where(Payment.created_at >= now - timedelta(hours=24))
                        .group_by(Payment.status)).all()
    counts = dict.fromkeys(PAYMENT_STATUSES, 0) | {status: count for status, count in rows}
    oldest = session.exec(select(func.min(Payment.created_at)).where(Payment.status == "pending")).one()
    age = None if oldest is None else max(0, int((now - _utc(oldest)).total_seconds()))
    return {"last_24_hours": counts, "oldest_pending_seconds": age}
