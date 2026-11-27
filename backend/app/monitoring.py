"""Opt-in Sentry reporting with allowlists, rather than best-effort PII removal.

Errors keep only a generic message and metadata tags. Performance traces (off unless
SENTRY_TRACES_SAMPLE_RATE is above 0) keep route templates, timings and SQL text with
placeholders: never URLs, query strings, headers, bodies, users or bound values.
"""
import os
import re
import time
from contextlib import contextmanager
from urllib.parse import urlsplit

import sentry_sdk

from .config import deploy_environment

ERROR_TAGS = ("request_id", "component", "route", "job", "deploy_environment")
TRANSACTION_TAGS = ("component", "job", "deploy_environment")
TRACE_KEYS = ("trace_id", "span_id", "parent_span_id", "op", "status", "origin")
SPAN_KEYS = ("span_id", "parent_span_id", "trace_id", "op", "start_timestamp", "timestamp", "status", "origin")
# Span descriptions are kept only for these operations: SQL text uses placeholders, and job
# names are our own constants. Anything else (URLs, templates, function arguments) is dropped.
DESCRIBED_OPS = ("db", "job")
# Starlette names a transaction after its route template, or after the raw path when no route matched.
TEMPLATE_SOURCES = {"route", "component", "task", "custom"}
RELEASE = re.compile(r"[A-Za-z0-9._@+-]{1,200}")
ENVIRONMENT = re.compile(r"[a-z0-9-]{1,32}")


CHECK_IN_KEYS = ("type", "check_in_id", "monitor_slug", "status", "duration", "monitor_config",
                 "environment", "release", "event_id", "timestamp")


def scrub_event(event, hint):
    if event.get("type") == "check_in":
        # Cron check-ins also pass through before_send: our job name, status, duration and schedule.
        return {key: event[key] for key in CHECK_IN_KEYS if key in event}
    # No request, user, headers, breadcrumbs, local variables, paths or exception messages.
    result = {key: event[key] for key in ("event_id", "timestamp", "platform", "level", "environment", "release")
              if key in event}
    tags = event.get("tags", {})
    result["message"] = "Taasheera job error" if "job" in tags else "Taasheera service error"
    result["tags"] = {key: tags[key] for key in ERROR_TAGS if key in tags}
    # Errors group per component and route/job, since messages and stacks are never sent.
    if "route" in result["tags"] or "job" in result["tags"]:
        result["fingerprint"] = ["taasheera", str(result["tags"].get("component", "")),
                                 str(result["tags"].get("route") or result["tags"].get("job"))]
    trace = (event.get("contexts") or {}).get("trace") or {}
    if trace.get("trace_id"):
        # Links the error to its performance trace; identifiers only.
        result["contexts"] = {"trace": {key: trace[key] for key in ("trace_id", "span_id") if key in trace}}
    return result


def _scrub_span(span):
    result = {key: span[key] for key in SPAN_KEYS if key in span}
    op = str(span.get("op") or "")
    if span.get("description") and op.startswith(DESCRIBED_OPS):
        result["description"] = str(span["description"])[:500]
    return result


def scrub_transaction(event, hint):
    info = event.get("transaction_info") or {}
    templated = info.get("source") in TEMPLATE_SOURCES
    result = {key: event[key] for key in ("event_id", "type", "timestamp", "start_timestamp", "platform",
                                          "environment", "release", "measurements") if key in event}
    result["transaction"] = event.get("transaction") if templated else "unmatched route"
    result["transaction_info"] = {"source": info.get("source") if templated else "custom"}
    contexts = event.get("contexts") or {}
    result["contexts"] = {"trace": {key: value for key, value in (contexts.get("trace") or {}).items()
                                    if key in TRACE_KEYS}}
    status_code = (contexts.get("response") or {}).get("status_code")
    if isinstance(status_code, int):
        result["contexts"]["response"] = {"status_code": status_code}
    tags = event.get("tags") or {}
    result["tags"] = {key: str(tags[key])[:64] for key in TRANSACTION_TAGS if key in tags}
    result["spans"] = [_scrub_span(span) for span in event.get("spans") or []]
    return result


def monitoring_enabled() -> bool:
    return bool(os.environ.get("SENTRY_DSN", "").strip())


def _sample_rate() -> float:
    raw = os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0").strip() or "0"
    try:
        rate = float(raw)
    except ValueError:
        raise RuntimeError("SENTRY_TRACES_SAMPLE_RATE must be a number between 0 and 1.") from None
    if not 0 <= rate <= 1:
        raise RuntimeError("SENTRY_TRACES_SAMPLE_RATE must be a number between 0 and 1.")
    return rate


def _release() -> str | None:
    release = os.environ.get("SENTRY_RELEASE", "").strip()
    if release and not RELEASE.fullmatch(release):
        raise RuntimeError("SENTRY_RELEASE may contain only letters, digits and . _ @ + -")
    return release or None


def _environment() -> str:
    environment = os.environ.get("SENTRY_ENVIRONMENT", "").strip() or deploy_environment()
    if not ENVIRONMENT.fullmatch(environment):
        raise RuntimeError("SENTRY_ENVIRONMENT may contain only lowercase letters, digits and hyphens.")
    return environment


def configure_monitoring(component: str = "api"):
    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn:
        return None
    try:
        parsed = urlsplit(dsn)
        valid = parsed.scheme == "https" and parsed.hostname and parsed.username and parsed.path.strip("/")
    except ValueError:
        valid = False
    if not valid:
        raise RuntimeError("SENTRY_DSN must be an HTTPS Sentry DSN.")
    rate = _sample_rate()
    integrations = []
    if rate > 0:
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
        # Database spans. HTTP transactions come from install_tracing(): Sentry's Starlette
        # integration would also patch Jinja2 templates, which this app does not install.
        integrations = [SqlalchemyIntegration()]
    sentry_sdk.init(dsn=dsn, environment=_environment(), release=_release(),
        send_default_pii=False, default_integrations=False, auto_enabling_integrations=False,
        integrations=integrations, before_send=scrub_event, before_send_transaction=scrub_transaction,
        traces_sample_rate=rate, max_request_body_size="never", max_breadcrumbs=0,
        include_local_variables=False, include_source_context=False)
    sentry_sdk.set_tag("component", component)
    sentry_sdk.set_tag("deploy_environment", deploy_environment())
    return sentry_sdk.get_client()


def install_tracing(app, client) -> None:
    """One Sentry transaction per HTTP request, named after its route template once routing has
    run ("/applications/{application_id}"); requests no route matched become "unmatched route"."""
    if client is None or not client.options.get("traces_sample_rate"):
        return
    from sentry_sdk.integrations.asgi import SentryAsgiMiddleware
    # Added last, so it wraps every other middleware and times the whole request.
    app.add_middleware(SentryAsgiMiddleware, transaction_style="url", mechanism_type="taasheera_asgi")


def current_trace_id() -> str | None:
    """The trace this request belongs to, so a log line can be found in Sentry."""
    if not monitoring_enabled():
        return None
    span = sentry_sdk.get_current_span()
    return getattr(span, "trace_id", None)


def capture_service_error(request):
    if not monitoring_enabled():
        return
    with sentry_sdk.new_scope() as scope:
        scope.set_tag("request_id", request.state.request_id)
        scope.set_tag("component", "api")
        route = getattr(request.scope.get("route"), "path", None)
        if route:
            scope.set_tag("route", route[:120])
        sentry_sdk.capture_message("Taasheera service error", level="error")


def capture_job_error(job: str):
    if not monitoring_enabled():
        return
    with sentry_sdk.new_scope() as scope:
        scope.set_tag("job", job)
        sentry_sdk.capture_message("Taasheera job error", level="error")


@contextmanager
def monitored_job(job: str, *, interval_seconds: int | None = None):
    """Trace one run of a background job and, with an interval, check in to a Sentry cron
    monitor of the same name, so a job that stops running raises an alert."""
    if not monitoring_enabled():
        yield
        return
    from sentry_sdk.crons import capture_checkin
    from sentry_sdk.crons.consts import MonitorStatus

    check_in = None
    if interval_seconds:
        check_in = capture_checkin(monitor_slug=job, status=MonitorStatus.IN_PROGRESS, monitor_config={
            "schedule": {"type": "interval", "value": max(1, interval_seconds // 60), "unit": "minute"},
            "checkin_margin": 5, "max_runtime": 30, "failure_issue_threshold": 2, "recovery_threshold": 1,
        })
    started = time.monotonic()
    with sentry_sdk.start_transaction(op="job", name=job) as transaction:
        transaction.set_tag("job", job)
        try:
            yield
        except Exception:
            transaction.set_status("internal_error")
            capture_job_error(job)
            if check_in:
                capture_checkin(monitor_slug=job, check_in_id=check_in, status=MonitorStatus.ERROR,
                                duration=time.monotonic() - started)
            raise
    if check_in:
        capture_checkin(monitor_slug=job, check_in_id=check_in, status=MonitorStatus.OK,
                        duration=time.monotonic() - started)
