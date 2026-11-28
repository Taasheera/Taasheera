"""Operator health dashboard data (Sprint 5 infrastructure monitoring): admin only.

Resource, latency, queue, email, payment and storage numbers for /backoffice. Counts, sizes and
timings only: never traveler names, emails, IDs, file names or document contents.
"""
import os
import shutil
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlmodel import Session, func, select

from .admin_api import require_role
from .config import deploy_environment
from .models import AgentRun, Notification

Level = Literal["ok", "warn", "fail"]
_cpu_lock = threading.Lock()
_cpu_sample: tuple[float, int, int] | None = None  # (monotonic time, busy jiffies, total jiffies)


class Check(BaseModel):
    component: str
    status: Level
    detail: str


class SystemHealth(BaseModel):
    status: Level
    checked_at: datetime
    release: str
    environment: str
    process: dict
    api: dict
    host: dict
    database: dict
    queue: dict
    notifications: dict
    payments: dict | None
    storage: dict
    checks: list[Check]


def _read_proc(path: str) -> str | None:
    try:
        return Path(path).read_text()
    except OSError:
        return None


def _cpu_jiffies() -> tuple[int, int] | None:
    text_ = _read_proc("/proc/stat")
    if not text_:
        return None
    values = [int(value) for value in text_.splitlines()[0].split()[1:]]
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return sum(values) - idle, sum(values)


def cpu_percent() -> float | None:
    """Host CPU use since the previous call (or over a short sample on the first call). Linux only."""
    global _cpu_sample
    with _cpu_lock:
        now = _cpu_jiffies()
        if now is None:
            return None
        previous = _cpu_sample
        if previous is None or time.monotonic() - previous[0] < 1:
            time.sleep(0.25)
            previous = (time.monotonic(), *now)
            now = _cpu_jiffies()
        _cpu_sample = (time.monotonic(), *now)
        busy, total = now[0] - previous[1], now[1] - previous[2]
        return round(100 * busy / total, 1) if total > 0 else None


def memory() -> dict:
    text_ = _read_proc("/proc/meminfo")
    if not text_:
        return {"total_mb": None, "available_mb": None, "used_percent": None, "swap_used_mb": None}
    values = {}
    for line in text_.splitlines():
        key, _, rest = line.partition(":")
        if rest.strip():
            values[key] = int(rest.split()[0]) // 1024
    total, available = values.get("MemTotal"), values.get("MemAvailable")
    return {"total_mb": total, "available_mb": available,
            "used_percent": round(100 * (total - available) / total, 1) if total and available is not None else None,
            "swap_used_mb": (values.get("SwapTotal", 0) - values.get("SwapFree", 0)) if "SwapTotal" in values else None}


def process_rss_mb() -> float | None:
    text_ = _read_proc("/proc/self/status")
    for line in (text_ or "").splitlines():
        if line.startswith("VmRSS:"):
            return round(int(line.split()[1]) / 1024, 1)
    return None


def disk(path: Path) -> dict:
    target = path
    while not target.exists() and target != target.parent:
        target = target.parent
    usage = shutil.disk_usage(target)
    return {"total_gb": round(usage.total / 2**30, 2), "free_gb": round(usage.free / 2**30, 2),
            "used_percent": round(100 * usage.used / usage.total, 1) if usage.total else None}


def database(engine) -> dict:
    started = time.perf_counter()
    reachable = True
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception:
        reachable = False
    latency = round((time.perf_counter() - started) * 1000, 2)
    backend = engine.url.get_backend_name()
    size = None
    revision = None
    try:
        if backend == "sqlite" and engine.url.database:
            base = Path(engine.url.database)
            size = sum(candidate.stat().st_size for candidate in (base, base.with_name(base.name + "-wal"))
                       if candidate.exists())
        elif backend == "postgresql":
            with engine.connect() as connection:
                size = connection.execute(text("SELECT pg_database_size(current_database())")).scalar()
        with engine.connect() as connection:
            revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
    except Exception:
        pass  # Tests build the schema without Alembic; the probe above is what matters.
    return {"reachable": reachable, "probe_ms": latency, "backend": backend,
            "size_mb": round(size / 2**20, 2) if size is not None else None, "schema_revision": revision}


def _age_seconds(value: datetime | None, now: datetime) -> int | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return max(0, int((now - value).total_seconds()))


def agent_queue(session: Session, now: datetime) -> dict:
    counts = dict(session.exec(select(AgentRun.status, func.count()).where(
        AgentRun.status.in_(["queued", "running"])).group_by(AgentRun.status)).all())
    oldest = session.exec(select(func.min(AgentRun.created_at)).where(AgentRun.status == "queued")).one()
    hour = now - timedelta(hours=1)
    finished = dict(session.exec(select(AgentRun.status, func.count()).where(
        AgentRun.finished_at >= hour).group_by(AgentRun.status)).all())
    return {"queued": counts.get("queued", 0), "running": counts.get("running", 0),
            "oldest_queued_seconds": _age_seconds(oldest, now), "finished_last_hour": finished}


def email_backlog(session: Session, now: datetime) -> dict:
    pending = session.exec(select(func.count()).select_from(Notification)
                           .where(Notification.email_status == "pending")).one()
    oldest = session.exec(select(func.min(Notification.created_at))
                          .where(Notification.email_status == "pending")).one()
    failed = session.exec(select(func.count()).select_from(Notification).where(
        Notification.email_status == "failed", Notification.created_at >= now - timedelta(days=1))).one()
    return {"email_pending": pending, "email_failed_last_day": failed,
            "oldest_pending_seconds": _age_seconds(oldest, now)}


def payments(session: Session) -> dict | None:
    try:
        from .payments.health import payment_health  # Present once payments are released.
    except ImportError:
        return None
    return payment_health(session)


def storage(root: Path) -> dict:
    writable = True
    try:
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=root, prefix=".health-", delete=True) as probe:
            probe.write(b"ok")
            probe.flush()
    except OSError:
        writable = False
    return {"writable": writable, **disk(root)}


def evaluate(api: dict, host: dict, db: dict, queue: dict, mail: dict, store: dict,
             pay: dict | None) -> list[Check]:
    checks: list[Check] = []

    def add(component: str, status: Level, detail: str):
        checks.append(Check(component=component, status=status, detail=detail))

    five = api["last_5m"]
    if five["requests"] and five["error_rate"] > 0.05:
        add("api", "fail", "More than 5% of requests failed in the last 5 minutes.")
    elif five["requests"] and (five["error_rate"] > 0.01 or (five["p95_ms"] or 0) > 2000):
        add("api", "warn", "Errors above 1% or p95 latency above 2 s in the last 5 minutes.")
    else:
        add("api", "ok", "Error rate and latency within targets.")
    if not db["reachable"]:
        add("database", "fail", "The database did not answer.")
    elif db["probe_ms"] > 200:
        add("database", "warn", "The database answered slowly.")
    else:
        add("database", "ok", "The database answered quickly.")
    mem, cpu = host["memory"], host["cpu_percent"]
    if mem["used_percent"] is not None and mem["used_percent"] > 95:
        add("memory", "fail", "Less than 5% of memory is free.")
    elif mem["used_percent"] is not None and mem["used_percent"] > 85:
        add("memory", "warn", "Less than 15% of memory is free.")
    else:
        add("memory", "ok", "Memory has headroom.")
    if cpu is not None and cpu > 90:
        add("cpu", "warn", "CPU above 90%.")
    else:
        add("cpu", "ok", "CPU has headroom.")
    for name, info in (("data disk", store), ("root disk", host["root_disk"])):
        used = info.get("used_percent") or 0
        add(name, "fail" if used > 95 else "warn" if used > 85 else "ok",
            "Disk above 95%." if used > 95 else "Disk above 85%." if used > 85 else "Disk has space.")
    if not store["writable"]:
        add("storage", "fail", "Document storage is not writable.")
    oldest = queue["oldest_queued_seconds"]
    add("agent queue", "warn" if oldest is not None and oldest > 600 else "ok",
        "A form-filling run has waited over 10 minutes." if oldest is not None and oldest > 600
        else "Form-filling runs are being picked up.")
    mail_age = mail["oldest_pending_seconds"]
    add("email", "warn" if (mail_age or 0) > 3600 or mail["email_failed_last_day"] else "ok",
        "Emails are waiting over an hour or failed today." if (mail_age or 0) > 3600 or mail["email_failed_last_day"]
        else "Emails are being sent.")
    if pay is not None:
        stuck = pay.get("oldest_pending_seconds")
        add("payments", "warn" if stuck is not None and stuck > 1800 else "ok",
            "A payment has been pending over 30 minutes." if stuck is not None and stuck > 1800
            else "Payments are completing.")
    return checks


def create_system_health_router(get_session, current_traveler, passport_root: Path) -> APIRouter:
    admin = require_role(current_traveler, "admin")
    router = APIRouter(prefix="/admin", tags=["Admin"], dependencies=[Depends(admin)])

    @router.get("/system-health", response_model=SystemHealth)
    def system_health(request: Request, session: Annotated[Session, Depends(get_session)]):
        now = datetime.now(timezone.utc)
        stats = request.app.state.request_stats
        api = {"in_flight": stats.in_flight, "last_1m": stats.summary(60), "last_5m": stats.summary(300),
               "last_15m": stats.summary(900)}
        load = os.getloadavg() if hasattr(os, "getloadavg") else None
        host = {"cpu_count": os.cpu_count(), "cpu_percent": cpu_percent(),
                "load_average": [round(value, 2) for value in load] if load else None,
                "memory": memory(), "root_disk": disk(Path("/").resolve())}
        db = database(request.app.state.engine)
        queue = agent_queue(session, now)
        mail = email_backlog(session, now)
        pay = payments(session)
        store = storage(passport_root)
        checks = evaluate(api, host, db, queue, mail, store, pay)
        overall: Level = ("fail" if any(check.status == "fail" for check in checks)
                          else "warn" if any(check.status == "warn" for check in checks) else "ok")
        return SystemHealth(
            status=overall, checked_at=now,
            release=os.environ.get("SENTRY_RELEASE", "").strip() or "unknown",
            environment=deploy_environment(),
            process={"pid": os.getpid(), "uptime_seconds": int(time.time() - stats.started),
                     "rss_mb": process_rss_mb(), "workers": int(os.environ.get("WEB_CONCURRENCY", "1") or 1)},
            api=api, host=host, database=db, queue=queue, notifications=mail, payments=pay,
            storage=store, checks=checks)

    return router
