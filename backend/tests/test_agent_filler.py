"""The form-filling agent against the local copy of the harmonised Schengen form (CMPS271-67, CMPS271-70).

The mapping tests are pure. The browser tests drive a real headless Chromium and skip when
Playwright's browser isn't installed (python -m playwright install chromium-headless-shell).
"""

from dataclasses import replace

import pytest

from app.agent.filler import fill
from app.agent.sites import REPLICA_DIR, SCHENGEN_HARMONISED
from app.agent.values import schengen_values

# ICAO 9303 specimen passport ("Utopia"). Never use real passport data in tests.
ANSWERS = {
    "main_destination": "D", "purpose": "tourism", "stay_over_90_days": False,
    "passport": {"surname": "ERIKSSON", "given_names": "ANNA MARIA", "document_number": "L898902C3",
                 "nationality": "UTO", "issuing_country": "UTO", "birth_date": "1974-08-12", "sex": "F",
                 "expiry_date": "2030-04-15", "personal_number": ""},
    "passport_issue_date": "2020-04-16", "surname_at_birth": "", "place_of_birth": "Zenith",
    "country_of_birth": "UTO", "marital_status": "single",
    "home_address": {"line1": "1 Example Street", "line2": "", "city": "Beirut", "region": "", "postal_code": "",
                     "country": "LBN"},
    "phone": "+96171123456", "occupation": "employed", "employer": "Example Company",
    "arrival_date": "2027-01-10", "departure_date": "2027-01-24", "entries": "single",
    "accommodation": "hotel", "accommodation_details": "Hotel Example, 1 Rue Exemple, Paris",
    "funding": "self", "previous_fingerprints": False,
}
EMAIL = "traveler@example.com"


def values(answers=ANSWERS):
    return schengen_values(answers, email=EMAIL, site=SCHENGEN_HARMONISED)


def test_values_come_from_the_confirmed_answers_in_the_form_s_formats():
    form = values()
    assert form["surname"] == "ERIKSSON" and form["first_names"] == "ANNA MARIA"
    assert form["date_of_birth"] == "12/08/1974" and form["valid_until"] == "15/04/2030"
    assert form["issue_date"] == "16/04/2020" and form["arrival_date"] == "10/01/2027"
    assert form["main_destination"] == "DEU"  # ICAO D for Germany becomes ISO DEU
    assert form["home_address"] == "1 Example Street\nBeirut\nLBN"
    assert form["fingerprints"] == "no" and form["cost_covered_by"] == "applicant"
    assert form["occupation"] == "Employee" and form["employer"] == "Example Company"
    assert form["email"] == EMAIL and form["document_type"] == "ordinary"
    # Empty and unanswered values are left out, never typed as blanks.
    assert "surname_at_birth" not in form and "sponsor" not in form and "fingerprints_date" not in form


def test_hosted_and_sponsored_trips_name_the_host_and_sponsor():
    form = values({**ANSWERS, "accommodation": "host", "host_details": "Lina Example, Paris",
                   "funding": "sponsor", "sponsor_details": "My aunt", "previous_fingerprints": True,
                   "fingerprints_date": "2024-05-01"})
    assert form["accommodation"] == "Lina Example, Paris"
    assert form["cost_covered_by"] == "sponsor" and form["sponsor"] == "My aunt"
    assert form["fingerprints"] == "yes" and form["fingerprints_date"] == "01/05/2024"


def test_every_site_field_has_a_value_key():
    keys = {field.key for page in SCHENGEN_HARMONISED.pages for field in page.fields}
    assert keys <= set(values()) | {"surname_at_birth", "fingerprints_date", "sponsor"}


# --- In a real browser --------------------------------------------------------


class Recorder:
    def __init__(self):
        self.entries, self.shots = [], []

    def entry(self, entry):
        self.entries.append(entry)

    def screenshot(self, shot):
        self.shots.append(shot)


@pytest.fixture(scope="module")
def browser():
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except Exception as error:  # noqa: BLE001
            pytest.skip(f"Chromium for Playwright isn't installed: {type(error).__name__}")
        yield browser
        browser.close()


@pytest.fixture
def page(browser):
    page = browser.new_page()
    yield page
    page.close()


def site_with(tmp_path, change=None):
    """The bundled form, optionally altered, served from a temporary copy."""
    html = (REPLICA_DIR / "schengen-harmonised.html").read_text()
    if change:
        html = change(html)
    copy = tmp_path / "form.html"
    copy.write_text(html)
    return replace(SCHENGEN_HARMONISED, start_url=copy.as_uri())


def run(page, site, form=None):
    recorder = Recorder()
    page.goto(site.start_url)
    return fill(page, site, values() if form is None else form, recorder), recorder


def test_the_agent_fills_every_page_and_stops_before_submitting(page, tmp_path):
    outcome, recorder = run(page, site_with(tmp_path))
    assert outcome.status == "ready"
    assert [shot.page_title for shot in recorder.shots] == [
        "Personal details", "Travel document", "Contact details and occupation", "Your journey",
        "Declaration and submission"]
    assert all(shot.png.startswith(b"\x89PNG") for shot in recorder.shots)
    typed = {entry.field: entry for entry in recorder.entries}
    assert typed["1. Surname"].value == "ERIKSSON" and typed["1. Surname"].page == 1
    assert typed["25. Member State of main destination"].value == "Germany"  # the label, not the code
    assert typed["9. Sex"].value == "Female"
    assert typed["14. Number of travel document"].page_title == "Travel document"
    assert len(recorder.entries) == len(values())
    # The values really are in the form, and nothing was submitted or declared.
    assert page.input_value("#document_number") == "L898902C3"
    assert page.input_value("#main_destination") == "DEU"
    assert page.evaluate("window.submitted") is False
    assert page.is_checked("#declaration") is False
    assert page.locator("section.current h2").text_content() == "Declaration and submission"


def test_a_captcha_stops_the_agent(page, tmp_path):
    site = site_with(tmp_path, lambda html: html.replace(
        '<h2>Contact details and occupation</h2>',
        '<h2>Contact details and occupation</h2><div class="g-recaptcha" data-sitekey="test" style="width:304px;height:78px"></div>'))
    outcome, recorder = run(page, site)
    assert outcome.status == "stopped" and "CAPTCHA" in outcome.message
    assert [shot.page for shot in recorder.shots] == [1, 2]
    assert page.evaluate("window.submitted") is False


def test_an_unknown_required_field_stops_the_agent(page, tmp_path):
    site = site_with(tmp_path, lambda html: html.replace(
        '<label for="issued_by">',
        '<label for="mother_name">Mother\'s maiden name</label><input type="text" id="mother_name" name="mother_name" required>'
        '<label for="issued_by">'))
    outcome, recorder = run(page, site)
    assert outcome.status == "stopped"
    assert outcome.message == "the form asks for something Ta'asheera doesn't know yet: \"Mother's maiden name\""
    assert [shot.page for shot in recorder.shots] == [1]


def test_a_changed_form_stops_the_agent(page, tmp_path):
    site = site_with(tmp_path, lambda html: html.replace('name="document_number"', 'name="passport_no"'))
    outcome, _ = run(page, site)
    assert outcome.status == "stopped" and "has changed" in outcome.message and "14. Number" in outcome.message


def test_a_missing_answer_stops_the_agent(page, tmp_path):
    form = values()
    del form["phone"]
    outcome, _ = run(page, site_with(tmp_path), form)
    assert outcome == type(outcome)("stopped", "your application has no answer for \"19. Telephone number\"")


def test_an_answer_the_form_has_no_option_for_stops_the_agent(page, tmp_path):
    outcome, _ = run(page, site_with(tmp_path), {**values(), "civil_status": "complicated"})
    assert outcome.status == "stopped" and "no option" in outcome.message and "10. Civil status" in outcome.message
    # The value itself is never repeated in the message.
    assert "complicated" not in outcome.message


def test_a_missing_submit_button_is_a_changed_form(page, tmp_path):
    site = site_with(tmp_path, lambda html: html.replace('id="submit"', 'id="send"'))
    outcome, recorder = run(page, site)
    assert outcome.status == "stopped" and "no submit button" in outcome.message


def test_a_site_that_does_not_load_fails_the_run(page, tmp_path):
    site = replace(SCHENGEN_HARMONISED, start_url=(tmp_path / "missing.html").as_uri())
    recorder = Recorder()
    try:
        page.goto(site.start_url)
    except Exception:  # noqa: BLE001 - the worker treats a failed load the same way
        pass
    outcome = fill(page, site, values(), recorder)
    assert outcome.status in {"stopped", "failed"} and recorder.entries == []
