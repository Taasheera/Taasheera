"""Fill an embassy's online form in a headless browser page, page by page, and stop before submitting.

Called by the worker with a Playwright page (CMPS271-67). On each form page it:

1. stops if a CAPTCHA appears: only the traveler may answer one (CAPTCHA hand-over is CMPS271-68);
2. fills every field it knows from the confirmed application, recording each one (CMPS271-70);
3. stops if a required field it doesn't know is still empty, rather than guess;
4. takes a screenshot, then moves to the next page.

On the last page it checks the submit button is there and stops without touching it or the
declaration: the traveler reviews and submits (US 18). Every stop has a fixed message for the
traveler; messages name form labels, never the values typed.
"""

from dataclasses import dataclass
from time import sleep
from urllib.parse import urlsplit
from typing import Literal, Protocol

from playwright.sync_api import TimeoutError as BrowserTimeout

from .sites import FormField, FormSite

CAPTCHA_SELECTOR = ", ".join([
    "iframe[src*='recaptcha']", "iframe[src*='hcaptcha']", "iframe[src*='turnstile']",
    "iframe[title*='captcha' i]", ".g-recaptcha", ".h-captcha", ".cf-turnstile",
    "[id*='captcha' i]", "[class*='captcha' i]", "[name*='captcha' i]",
])
STEP_TIMEOUT_MS = 10_000
FORM_CHANGED = "the form has changed since Ta'asheera last saw it"
RETRY_DELAYS = (0.5, 1.5)

# Required, visible and empty inputs on the current page that the agent doesn't know.
_UNKNOWN_REQUIRED = """([scope, known]) => {
  const root = document.querySelector(scope) || document.body
  const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)
  const label = el => {
    const byFor = el.id && document.querySelector(`label[for="${CSS.escape(el.id)}"]`)
    const legend = el.closest('fieldset')?.querySelector('legend')
    return ((el.type === 'radio' ? legend : byFor) || legend || {}).textContent?.trim() || el.name || 'a field'
  }
  const missing = new Set()
  for (const el of root.querySelectorAll('input, select, textarea')) {
    if (!el.required || !visible(el) || known.includes(el.name) || el.type === 'hidden') continue
    const empty = el.type === 'radio'
      ? !root.querySelector(`input[type=radio][name="${CSS.escape(el.name)}"]:checked`)
      : el.type === 'checkbox' ? !el.checked : !el.value
    if (empty) missing.add(label(el))
  }
  return [...missing]
}"""


@dataclass(frozen=True)
class Entry:
    page: int
    page_title: str
    field: str
    value: str


@dataclass(frozen=True)
class Shot:
    page: int
    page_title: str
    png: bytes


@dataclass(frozen=True)
class Outcome:
    status: Literal["ready", "stopped", "failed"]
    message: str


class Recorder(Protocol):
    def entry(self, entry: Entry) -> None: ...
    def screenshot(self, shot: Shot) -> None: ...


class _Stop(Exception):
    def __init__(self, message: str, status: Literal["stopped", "failed"] = "stopped"):
        super().__init__(message)
        self.outcome = Outcome(status, message)


def _set(page, field: FormField, value: str) -> None:
    if field.kind == "text":
        page.locator(field.selector).filter(visible=True).first.fill(value, timeout=STEP_TIMEOUT_MS)
    elif field.kind == "select":
        if page.locator(f"{field.selector} option[value=\"{value}\"]").count() == 0:
            raise _Stop(f"the form has no option that matches your answer for \"{field.label}\"")
        page.locator(field.selector).filter(visible=True).first.select_option(value, timeout=STEP_TIMEOUT_MS)
    else:
        option = page.locator(f"{field.selector}[value=\"{value}\"]")
        if option.count() == 0:
            raise _Stop(f"the form has no option that matches your answer for \"{field.label}\"")
        option.first.check(timeout=STEP_TIMEOUT_MS)


def _shown_value(page, field: FormField, value: str) -> str:
    """What the traveler would see in the field: an option's label rather than its code."""
    if field.kind == "select":
        label = page.locator(f"{field.selector} option[value=\"{value}\"]").first.text_content() or value
        return label.strip()
    if field.kind == "radio":
        option = page.locator(f"{field.selector}[value=\"{value}\"]").first
        option_id = option.get_attribute("id")
        if option_id:
            label = page.locator(f"label[for=\"{option_id}\"]").first
            if label.count():
                return (label.text_content() or value).strip()
    return value


def _check_captcha(page) -> None:
    # Visible ones only: a multi-step form may hold later steps' widgets in the page already.
    if page.locator(CAPTCHA_SELECTOR).filter(visible=True).count() > 0:
        raise _Stop("the site asked for a CAPTCHA, which only you can answer")


class TransientPortalError(Exception):
    """A temporary response from the portal before filling begins."""


def retry_timeout(action, before_retry=lambda: None):
    for delay in RETRY_DELAYS:
        try:
            return action()
        except (BrowserTimeout, TransientPortalError):
            before_retry()
            sleep(delay)
    return action()


def _current(page, selector: str) -> bool:
    return page.locator(selector).filter(visible=True).count() > 0


def _next_page(page, site: FormSite, number: int, next_selector: str) -> None:
    # Clicking Next may have reached the portal even if Playwright timed out. Observe the next
    # page before continuing and never re-click an ambiguous action.
    button = page.locator(next_selector).filter(visible=True)
    if (button.count() != 1 or page.locator(site.submit).filter(visible=True).count()
            or not button.evaluate("el => el instanceof HTMLButtonElement && el.type === 'button'")):
        raise _Stop(f"{FORM_CHANGED} (the next action is missing or ambiguous)")
    expected = site.pages[number]
    marker = expected.ready or (", ".join(field.selector for field in expected.fields) if expected.fields
                                else site.submit)
    try:
        button.click(timeout=STEP_TIMEOUT_MS)
    except BrowserTimeout:
        pass
    try:
        retry_timeout(lambda: page.locator(marker).filter(visible=True).first.wait_for(
            state="visible", timeout=STEP_TIMEOUT_MS), lambda: _check_captcha(page))
    except BrowserTimeout:
        _check_captcha(page)
        raise _Stop(f"{FORM_CHANGED} (the next page did not appear)") from None


def fill(page, site: FormSite, values: dict[str, str], recorder: Recorder) -> Outcome:
    """Fill `site` in `page`, which the caller has opened at `site.start_url`. Never submits."""
    from .queue import RunCancelled

    page.set_default_timeout(STEP_TIMEOUT_MS)
    try:
        for number, form_page in enumerate(site.pages, start=1):
            retry_timeout(lambda: page.wait_for_load_state("domcontentloaded"))
            _check_captcha(page)
            expected, actual = urlsplit(site.start_url), urlsplit(page.url)
            if (actual.scheme, actual.netloc) != (expected.scheme, expected.netloc):
                raise _Stop("the site redirected outside the configured portal")
            known_names = []
            for field in form_page.fields:
                matching = page.locator(field.selector).filter(visible=True)
                count = matching.count()
                if not count:
                    raise _Stop(f'{FORM_CHANGED} ("{field.label}" is missing)')
                if field.kind != "radio" and count != 1:
                    raise _Stop(f'{FORM_CHANGED} ("{field.label}" is ambiguous)')
                known_names.extend(matching.evaluate_all("fields => fields.map(field => field.name)"))
                value = values.get(field.key)
                if value is None:
                    if field.required:
                        raise _Stop(f"your application has no answer for \"{field.label}\"")
                    continue
                retry_timeout(lambda: _set(page, field, value), lambda: _check_captcha(page))
                recorder.entry(Entry(number, form_page.title, field.label, _shown_value(page, field, value)))
            last = form_page.next is None
            if not last:
                unknown = page.evaluate(_UNKNOWN_REQUIRED, [site.page_scope, known_names])
                if unknown:
                    listed = ", ".join(f"\"{label}\"" for label in unknown[:3])
                    raise _Stop(f"the form asks for something Ta'asheera doesn't know yet: {listed}"[:250])
            _check_captcha(page)
            recorder.screenshot(Shot(number, form_page.title, retry_timeout(lambda: page.screenshot(full_page=True))))
            if last:
                if not _current(page, site.submit):
                    raise _Stop(f"{FORM_CHANGED} (no submit button at the end)")
                return Outcome("ready", "The form is filled and waiting for you to review and submit it.")
            _next_page(page, site, number, form_page.next)
        raise _Stop(f"{FORM_CHANGED} (it has fewer pages)")
    except RunCancelled:
        raise
    except _Stop as stop:
        return stop.outcome
    except Exception as error:  # noqa: BLE001 - any browser failure ends the run the same way
        # The type only: a Playwright message can quote the page's content.
        return Outcome("failed", f"the embassy site didn't respond as expected ({type(error).__name__})")
