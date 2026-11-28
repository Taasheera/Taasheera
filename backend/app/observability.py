"""Request metadata only; never serialize headers, URLs, bodies, or exceptions."""

import json
import logging
import math
import re
import threading
import time
from collections import deque
from contextvars import ContextVar
from uuid import uuid4

from fastapi import FastAPI, Request
from sqlalchemy import event


request_log = logging.getLogger("taasheera.requests")
# A caller (the load tests, a proxy) may name its request so its log line can be found;
# anything else gets a fresh ID. Letters, digits and hyphens only, so nothing can be injected.
REQUEST_ID = re.compile(r"[A-Za-z0-9-]{8,64}")
# Database time of the request being handled. Endpoints run in worker threads, which copy this
# context, so they add to the same dictionary the middleware created.
_request_metrics: ContextVar[dict | None] = ContextVar("taasheera_request_metrics", default=None)


def install_private_no_store(app: FastAPI) -> None:
    private_prefixes = ("/auth", "/passports", "/profile", "/sessions", "/activity", "/applications", "/assistant", "/notifications")

    @app.middleware("http")
    async def no_store(request: Request, call_next):
        response = await call_next(request)
        path, root = request.scope["path"], request.scope.get("root_path", "")
        if root and path.startswith(root + "/"):
            path = path[len(root):]
        if any(path == prefix or path.startswith(prefix + "/") for prefix in private_prefixes):
            # Preserve the explicit private directive used for passport images.
            response.headers.setdefault("Cache-Control", "no-store")
        return response


class RequestJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(record.activity, separators=(",", ":"))


def install_query_timing(engine) -> None:
    """Add each SQL statement's duration to the current request's metrics (none outside requests)."""

    @event.listens_for(engine, "before_cursor_execute")
    def started(conn, cursor, statement, parameters, context, executemany):
        conn.info.setdefault("taasheera_query_started", []).append(time.perf_counter())

    @event.listens_for(engine, "after_cursor_execute")
    def finished(conn, cursor, statement, parameters, context, executemany):
        stack = conn.info.get("taasheera_query_started")
        if not stack:
            return
        elapsed = time.perf_counter() - stack.pop()
        metrics = _request_metrics.get()
        if metrics is not None:
            metrics["db_seconds"] += elapsed
            metrics["queries"] += 1


def _percentile(ordered: list[float], fraction: float) -> float | None:
    """Nearest-rank percentile of an already sorted list."""
    if not ordered:
        return None
    index = min(len(ordered) - 1, max(0, math.ceil(fraction * len(ordered)) - 1))
    return round(ordered[index], 2)


class RequestStats:
    """Rolling per-process request numbers for the operator health page: 10-second buckets kept for
    15 minutes. Health checks are left out so probes do not hide real traffic."""

    BUCKET = 10
    KEEP = 900
    SAMPLES = 1000  # latencies kept per bucket; counts stay exact

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._buckets: deque[dict] = deque()
        self.started = time.time()
        self.in_flight = 0

    def begin(self) -> None:
        with self._lock:
            self.in_flight += 1
            bucket = self._bucket()
            bucket["peak_in_flight"] = max(bucket["peak_in_flight"], self.in_flight)

    def end(self, duration_ms: float, db_ms: float, status: int, *, counted: bool = True) -> None:
        with self._lock:
            self.in_flight = max(0, self.in_flight - 1)
            if not counted:
                return
            bucket = self._bucket()
            bucket["count"] += 1
            bucket["errors"] += status >= 500
            bucket["throttled"] += status == 429
            if len(bucket["durations"]) < self.SAMPLES:
                bucket["durations"].append(duration_ms)
                bucket["db"].append(db_ms)

    def _bucket(self) -> dict:
        now = int(self._clock() // self.BUCKET) * self.BUCKET
        if not self._buckets or self._buckets[-1]["start"] != now:
            self._buckets.append({"start": now, "count": 0, "errors": 0, "throttled": 0,
                                  "durations": [], "db": [], "peak_in_flight": self.in_flight})
        while self._buckets and self._buckets[0]["start"] < now - self.KEEP:
            self._buckets.popleft()
        return self._buckets[-1]

    def summary(self, seconds: int) -> dict:
        with self._lock:
            since = self._clock() - seconds
            buckets = [bucket for bucket in self._buckets if bucket["start"] + self.BUCKET > since]
            durations = sorted(value for bucket in buckets for value in bucket["durations"])
            database = sorted(value for bucket in buckets for value in bucket["db"])
            count = sum(bucket["count"] for bucket in buckets)
            errors = sum(bucket["errors"] for bucket in buckets)
            # A process younger than the window has served for less time than the window.
            span = max(1.0, min(seconds, time.time() - self.started))
            return {
                "window_seconds": seconds, "requests": count, "requests_per_second": round(count / span, 2),
                "error_rate": round(errors / count, 4) if count else 0.0,
                "throttled": sum(bucket["throttled"] for bucket in buckets),
                "p50_ms": _percentile(durations, 0.50), "p95_ms": _percentile(durations, 0.95),
                "p99_ms": _percentile(durations, 0.99), "db_p95_ms": _percentile(database, 0.95),
                "peak_in_flight": max((bucket["peak_in_flight"] for bucket in buckets), default=0),
            }


def install_request_logging(app: FastAPI) -> None:
    if not request_log.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(RequestJsonFormatter())
        request_log.addHandler(handler)
    request_log.setLevel(logging.INFO)
    request_log.propagate = False
    app.state.request_stats = RequestStats()

    @app.middleware("http")
    async def record_request(request: Request, call_next):
        supplied = request.headers.get("x-request-id", "")
        request_id = supplied if REQUEST_ID.fullmatch(supplied) else uuid4().hex
        request.state.request_id = request_id
        metrics = {"db_seconds": 0.0, "queries": 0}
        token = _request_metrics.set(metrics)
        stats: RequestStats = request.app.state.request_stats
        probe = request.scope["path"].startswith("/health/")
        stats.begin()
        started = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            _request_metrics.reset(token)
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            db_ms = round(metrics["db_seconds"] * 1000, 2)
            stats.end(duration_ms, db_ms, status_code, counted=not probe)
            if status_code >= 500:
                from .monitoring import capture_service_error
                capture_service_error(request)
                owner_id = getattr(request.state, "traveler_id", None)
                if owner_id is not None:
                    from sqlmodel import Session
                    from .activity import record_activity
                    try:
                        with Session(request.app.state.engine) as session:
                            record_activity(session, request.app.state.activity_directory, owner_id,
                                "api_error", getattr(request.scope.get("route"), "path", "unmatched")[:80], "error")
                    except Exception:
                        # Monitoring must preserve the original response/exception.
                        logging.getLogger(__name__).error("Could not persist service error metadata")
            # FastAPI has populated the route template, which contains no
            # traveler IDs, reset tokens, query strings, or submitted values.
            route = request.scope.get("route")
            route_path = getattr(route, "path", "unmatched")
            from .monitoring import current_trace_id
            line = {
                "request_id": request_id,
                "method": request.method,
                "route": route_path,
                "status": status_code,
                "duration_ms": duration_ms,
                "db_ms": db_ms,
                "db_queries": metrics["queries"],
            }
            trace_id = current_trace_id()
            if trace_id:
                line["trace_id"] = trace_id
            request_log.info("request", extra={"activity": line})
