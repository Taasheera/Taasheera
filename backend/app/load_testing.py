"""Staging-only stand-ins for the capacity tests (Sprint 5 whole-app capacity test).

ASSISTANT_FAKE_LATENCY_MS replaces Gemini with a fixed reply after that many milliseconds. The
assistant then costs what a real call costs the API (a worker thread held for the call), without
sending synthetic traffic to Google or spending the free tier. Refused outside staging.
"""
import os
import time

from .assistant import ModelTurn
from .config import deploy_environment

FAKE_REPLY = ("This is a load-test reply from the staging stand-in for the assistant. "
              "It is not legal or immigration advice.")


def fake_assistant_from_environment():
    """(ask, ask_tools) stand-ins, or None when ASSISTANT_FAKE_LATENCY_MS is not set."""
    raw = os.environ.get("ASSISTANT_FAKE_LATENCY_MS", "").strip()
    if not raw:
        return None
    if deploy_environment() != "staging":
        raise RuntimeError("ASSISTANT_FAKE_LATENCY_MS is allowed only on staging (DEPLOY_ENVIRONMENT=staging).")
    if not raw.isascii() or not raw.isdigit() or int(raw) > 30_000:
        raise RuntimeError("ASSISTANT_FAKE_LATENCY_MS must be a whole number of milliseconds up to 30000.")
    delay = int(raw) / 1000

    def ask(system: str, messages: list[dict]) -> str:
        time.sleep(delay)
        return FAKE_REPLY

    def ask_tools(system: str, messages: list[dict]) -> ModelTurn:
        time.sleep(delay)
        return ModelTurn(text=FAKE_REPLY)

    return ask, ask_tools
