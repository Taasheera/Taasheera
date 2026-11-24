"""The agent worker (CMPS271-236): a separate process that takes form-filling runs off the queue.

It runs apart from the web server so a slow or broken embassy site never ties up API requests,
and the browser's memory lives in its own container. Each run gets a fresh browser context
(no cookies or storage carried between travelers) that is closed when the run ends.
"""

import logging
import os
import time
from pathlib import Path
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import date

from sqlmodel import Session, select

from ..account_lock import account_lock
from ..activity import activity_directory_from_environment
from ..applications import flow
from ..applications.router import is_recorded
from ..models import AgentRun, Application, ApplicationAnswer, Traveler
from . import queue
from .filler import Entry, Outcome, Shot, TransientPortalError, fill, retry_timeout
from .sites import SITES
from .storage import AgentFiles
from .values import VALUE_BUILDERS

log = logging.getLogger("taasheera.agent")
POLL_SECONDS = 3
# Touched on every loop; the container's health check fails if it goes stale (deploy/aws/compose.yaml).
HEARTBEAT = Path(os.environ.get("AGENT_HEARTBEAT_FILE", "/tmp/taasheera-agent-alive"))
PAGE_LOAD_TIMEOUT_MS = 15_000

# Opens a browser and yields it; the worker asks it for a new page per run.
BrowserFactory = Callable[[], AbstractContextManager]


def form_values(session: Session, run: AgentRun) -> tuple[dict[str, str] | None, str | None]:
    """The values to type, from the application as it is now, or why the run can't go ahead."""
    application = session.get(Application, run.application_id)
    traveler = session.get(Traveler, run.traveler_id)
    if application is None or traveler is None or application.traveler_id != traveler.id:
        return None, "the application no longer exists"
    questionnaire = flow.load_questionnaire(application.questionnaire_id)
    rows = session.exec(select(ApplicationAnswer).where(ApplicationAnswer.application_id == application.id))
    answers, _ = flow.reconcile(questionnaire, {row.question_id: row.value for row in rows}, date.today())
    if traveler.status != "active":
        return None, "the traveler's account is unavailable"
    if is_recorded(application):
        return None, "the application is recorded as submitted"
    if application.archived_at is not None:
        return None, "the application is archived"
    if flow.next_question(questionnaire, answers) is not None or flow.stop_message(questionnaire, answers):
        return None, "your answers changed and aren't complete any more. Finish them, then start the agent again"
    site = SITES[run.site]
    return VALUE_BUILDERS[site.value_profile](answers, email=traveler.email, site=site), None


class _Recorder:
    def __init__(self, session: Session, files: AgentFiles, run: AgentRun):
        self.session, self.files, self.run = session, files, run
        self.owner_id, self.application_id, self.run_id = run.traveler_id, run.application_id, run.id
        self.lock_directory = activity_directory_from_environment().parent / ".account-locks"

    def entry(self, entry: Entry) -> None:
        queue.record_entry(self.session, self.run.id, page=entry.page, page_title=entry.page_title,
                           field=entry.field, value=entry.value)

    def screenshot(self, shot: Shot) -> None:
        # Serialize the file write with account and application deletion. Publish its row only
        # after the file is complete, so a live poll never sees a partially written screenshot.
        with account_lock(self.lock_directory, self.owner_id):
            queue.record_screenshot(self.session, self.run_id, page=shot.page, page_title=shot.page_title,
                                    save=lambda screenshot_id: self.files.save(self.owner_id, self.application_id,
                                                                               screenshot_id, shot.png),
                                    cleanup=lambda screenshot_id: self.files.remove_screenshot(
                                        self.owner_id, self.application_id, screenshot_id))


def _open_page(page, url: str):
    response = page.goto(url, timeout=PAGE_LOAD_TIMEOUT_MS, wait_until="domcontentloaded")
    if response is not None and response.status in {502, 503, 504}:
        raise TransientPortalError()
    if response is not None and response.status >= 400:
        raise RuntimeError("The configured page did not load.")


def process(session: Session, files: AgentFiles, run: AgentRun, browser) -> Outcome:
    """Fill one claimed run in a fresh browser context, record how it ended, and return that."""
    values, problem = form_values(session, run)
    if values is None:
        outcome = Outcome("stopped", problem)
    else:
        site = SITES[run.site]
        context = browser.new_context(viewport={"width": 1280, "height": 900}, locale="en-GB")
        try:
            page = context.new_page()
            retry_timeout(lambda: _open_page(page, site.start_url))
            outcome = fill(page, site, values, _Recorder(session, files, run))
        except queue.RunCancelled:
            outcome = Outcome("failed", "cancelled")
        except Exception as error:  # noqa: BLE001 - a site that won't load ends the run, not the worker
            outcome = Outcome("failed", f"the embassy site didn't load ({type(error).__name__})")
        finally:
            context.close()
    queue.finish(session, run.id, outcome.status, outcome.message)
    # Ids and statuses only: never values or page content.
    log.info("agent run finished", extra={"run_id": run.id, "status": outcome.status})
    return outcome


def run_once(engine, files: AgentFiles, open_browser: BrowserFactory) -> bool:
    """Process one queued run if there is one. True if it did.

    The browser starts only for a run and closes after it: measured at about 230 MB idle and
    430 MB while filling (scripts/measure_agent_memory.py), too much to keep on a small server.
    """
    with Session(engine) as session:
        run = queue.claim(session)
        if run is None:
            return False
        try:
            with open_browser() as browser:
                process(session, files, run, browser)
        except Exception as error:  # noqa: BLE001 - a browser that won't start fails this run, not the worker
            queue.finish(session, run.id, "failed", f"the agent's browser didn't start ({type(error).__name__})")
        return True


def serve(engine, files: AgentFiles, open_browser: BrowserFactory, *, stop: Callable[[], bool] = lambda: False):
    with Session(engine) as session:
        recovered = queue.recover_stale(session)
    if recovered:
        log.warning("recovered interrupted agent runs", extra={"count": recovered})
    while not stop():
        HEARTBEAT.touch()
        if not run_once(engine, files, open_browser):
            time.sleep(POLL_SECONDS)
