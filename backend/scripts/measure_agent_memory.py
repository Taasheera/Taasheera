"""Measure the headless browser's memory while the agent fills the bundled form (CMPS271-236).

python scripts/measure_agent_memory.py  — prints the peak resident memory of the browser
processes and of this Python process, so the team can choose where the worker runs. Synthetic
specimen data only; nothing is sent anywhere.
"""

import sys
import threading
import time
from pathlib import Path

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

from playwright.sync_api import sync_playwright  # noqa: E402

from app.agent.filler import fill  # noqa: E402
from app.agent.sites import SCHENGEN_HARMONISED  # noqa: E402
from test_agent_filler import Recorder, values  # noqa: E402

MB = 1024 * 1024


def browser_rss(root: psutil.Process) -> int:
    total = 0
    for process in root.children(recursive=True):
        try:
            total += process.memory_info().rss
        except psutil.Error:
            pass
    return total


def main() -> None:
    me = psutil.Process()
    peak = {"browser": 0, "idle": 0}
    stop = threading.Event()

    def sample():
        while not stop.is_set():
            peak["browser"] = max(peak["browser"], browser_rss(me))
            time.sleep(0.05)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=["--disable-dev-shm-usage"])
        time.sleep(1)
        peak["idle"] = browser_rss(me)
        watcher = threading.Thread(target=sample, daemon=True)
        watcher.start()
        for _ in range(3):
            context = browser.new_context(viewport={"width": 1280, "height": 900})
            page = context.new_page()
            page.goto(SCHENGEN_HARMONISED.start_url)
            outcome = fill(page, SCHENGEN_HARMONISED, values(), Recorder())
            context.close()
        stop.set()
        watcher.join()
        browser.close()
    print(f"outcome of last run: {outcome.status}")
    print(f"browser idle: {peak['idle'] / MB:.0f} MB; browser peak while filling: {peak['browser'] / MB:.0f} MB")
    print(f"worker Python process: {me.memory_info().rss / MB:.0f} MB")


if __name__ == "__main__":
    main()
