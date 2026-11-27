"""Run the form-filling agent worker (CMPS271-236): python agent_worker.py

Uses the same DATABASE_URL and PASSPORT_STORAGE_DIR as the API, and Playwright's headless
Chromium (python -m playwright install --with-deps chromium-headless-shell). Stop with Ctrl+C
or SIGTERM; a run interrupted mid-way is marked failed when a worker next starts.
"""

import logging
import os
import signal
from contextlib import contextmanager
from pathlib import Path

from app.agent.storage import AgentFiles
from app.agent.worker import serve
from app.database import build_engine
from app.monitoring import capture_job_error, configure_monitoring
from app.schema import require_current_schema

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


@contextmanager
def chromium():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        # --disable-dev-shm-usage: containers have a small /dev/shm, which crashes Chromium.
        browser = playwright.chromium.launch(args=["--disable-dev-shm-usage"])
        try:
            yield browser
        finally:
            browser.close()


def main() -> None:
    engine = build_engine(os.environ.get("DATABASE_URL"))
    require_current_schema(engine)
    root = Path(os.environ.get("PASSPORT_STORAGE_DIR", str(Path(__file__).resolve().parent / "storage" / "passports")))
    stopping = False

    def request_stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    # Same opt-in, allowlisted Sentry as the API, tagged so worker crashes alert separately.
    monitoring = configure_monitoring(component="worker")
    try:
        serve(engine, AgentFiles(root), chromium, stop=lambda: stopping)
    except Exception:
        capture_job_error("agent-worker")
        raise
    finally:
        if monitoring is not None:
            monitoring.flush(timeout=2)


if __name__ == "__main__":
    main()
