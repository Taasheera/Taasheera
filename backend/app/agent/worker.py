"""The agent worker (CMPS271-236): a separate process that takes form-filling runs off the queue.

It runs apart from the web server so a slow or broken embassy site never ties up API requests,
and the browser's memory lives in its own container. Each run gets a fresh browser context
(no cookies or storage carried between travelers) that is closed when the run ends.
"""

import logging
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import date

from sqlmodel import Session, select

from ..applications import flow
from ..models import AgentRun, Application, ApplicationAnswer, Traveler
from . import queue
from .filler import Entry, Outcome, Shot, fill
from .sites import SITES
from .storage import AgentFiles
from .values import VALUE_BUILDERS

log = logging.getLogger("taasheera.agent")
POLL_SECONDS = 3
PAGE_LOAD_TIMEOUT_MS = 45_000

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
    if application.archived_at is not None:
        return None, "the application is archived"
    if flow.next_question(questionnaire, answers) is not None or flow.stop_message(questionnaire, answers):
        return None, "your answers changed and aren't complete any more. Finish them, then start the agent again"
    site = SITES[run.site]
    return VALUE_BUILDERS[site.id](answers, email=traveler.email, site=site), None


class _Recorder:
    def __init__(self, session: Session, files: AgentFiles, run: AgentRun):
        self.session, self.files, self.run = session, files, run

    def entry(self, entry: Entry) -> None:
        queue.record_entry(self.session, self.run.id, page=entry.page, page_title=entry.page_title,
                           field=entry.field, value=entry.value)

    def screenshot(self, shot: Shot) -> None:
        screenshot_id = queue.record_screenshot(self.session, self.run.id, page=shot.page, page_title=shot.page_title)
        self.files.save(self.run.traveler_id, self.run.application_id, screenshot_id, shot.png)


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
            page.goto(site.start_url, timeout=PAGE_LOAD_TIMEOUT_MS, wait_until="domcontentloaded")
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


def run_once(engine, files: AgentFiles, browser) -> bool:
    """Process one queued run if there is one. True if it did."""
    with Session(engine) as session:
        run = queue.claim(session)
        if run is None:
            return False
        process(session, files, run, browser)
        return True


def serve(engine, files: AgentFiles, open_browser: BrowserFactory, *, stop: Callable[[], bool] = lambda: False):
    with Session(engine) as session:
        recovered = queue.recover_stale(session)
    if recovered:
        log.warning("recovered interrupted agent runs", extra={"count": recovered})
    with open_browser() as browser:
        while not stop():
            if not run_once(engine, files, browser):
                time.sleep(POLL_SECONDS)
