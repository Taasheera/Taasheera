"""The embassy online forms the agent knows, described as data (CMPS271-67).

Each site lists its pages and, per page, the fields the agent fills: a key into the values that
`values.py` works out from the application, the field's label as the traveler sees it, a CSS
selector and how to set it. Everything else on a page is unknown to the agent: if an unknown
field is required and empty, the agent stops instead of guessing. Selectors are the only part
tied to a site's HTML, so a site redesign is a data change here, caught by the agent stopping
with "the form has changed" rather than typing into the wrong box.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

REPLICA_DIR = Path(__file__).resolve().parent / "replicas"

Kind = Literal["text", "select", "radio"]


@dataclass(frozen=True)
class FormField:
    key: str
    label: str
    selector: str
    kind: Kind = "text"
    required: bool = True


@dataclass(frozen=True)
class FormPage:
    title: str
    fields: tuple[FormField, ...]
    # The button to the next page; None on the last page, which holds the submit button.
    next: str | None = "button[data-next]"


@dataclass(frozen=True)
class FormSite:
    id: str
    name: str
    questionnaires: frozenset[str]
    start_url: str
    pages: tuple[FormPage, ...]
    # Must exist on the last page, and is never clicked: the traveler submits.
    submit: str
    # Where the current page's inputs are, for the unknown-field check.
    page_scope: str = "body"
    date_format: str = "%d/%m/%Y"


def _replica(name: str) -> str:
    return (REPLICA_DIR / name).as_uri()


def _page(title: str, *fields: FormField, next: str | None = "section.current button[data-next]") -> FormPage:
    return FormPage(title=title, fields=fields, next=next)


def _f(key: str, label: str, kind: Kind = "text", required: bool = True) -> FormField:
    # Names are unique across the form, so the selector is the field's name attribute.
    selector = f"section.current [name='{key}']"
    return FormField(key=key, label=label, selector=selector, kind=kind, required=required)


SCHENGEN_HARMONISED = FormSite(
    id="schengen-harmonised",
    name="Harmonised Schengen visa application form",
    questionnaires=frozenset({"schengen-short-stay"}),
    # Points at the embassy portal in production once one is chosen; the bundled copy otherwise.
    start_url=os.environ.get("AGENT_SCHENGEN_FORM_URL", "") or _replica("schengen-harmonised.html"),
    page_scope="section.current",
    submit="section.current #submit",
    pages=(
        _page("Personal details",
              _f("surname", "1. Surname"), _f("surname_at_birth", "2. Surname at birth", required=False),
              _f("first_names", "3. First name(s)"), _f("date_of_birth", "4. Date of birth"),
              _f("place_of_birth", "5. Place of birth"), _f("country_of_birth", "6. Country of birth", "select"),
              _f("nationality", "7. Current nationality", "select"), _f("sex", "9. Sex", "radio"),
              _f("civil_status", "10. Civil status", "select")),
        _page("Travel document",
              _f("document_type", "13. Type of travel document", "select"),
              _f("document_number", "14. Number of travel document"), _f("issue_date", "15. Date of issue"),
              _f("valid_until", "16. Valid until"), _f("issued_by", "17. Issued by", "select")),
        _page("Contact details and occupation",
              _f("home_address", "19. Home address"), _f("email", "19. Email address"),
              _f("phone", "19. Telephone number"), _f("occupation", "21. Current occupation"),
              _f("employer", "22. Employer or school", required=False)),
        _page("Your journey",
              _f("purpose", "23. Purpose of the journey", "select"),
              _f("main_destination", "25. Member State of main destination", "select"),
              _f("entries", "27. Number of entries requested", "radio"),
              _f("arrival_date", "28. Intended date of arrival"), _f("departure_date", "29. Intended date of departure"),
              _f("fingerprints", "30. Fingerprints collected previously", "radio"),
              _f("fingerprints_date", "30. Date fingerprints were taken", required=False),
              _f("accommodation", "31. Accommodation or inviting person"),
              _f("cost_covered_by", "33. Cost of the stay covered by", "radio"),
              _f("sponsor", "33. Sponsor", required=False)),
        # The declaration is the traveler's own statement: the agent leaves it, and Submit, alone.
        _page("Declaration and submission", next=None),
    ),
)

SITES = {site.id: site for site in (SCHENGEN_HARMONISED,)}


def site_for(questionnaire_id: str) -> FormSite | None:
    return next((site for site in SITES.values() if questionnaire_id in site.questionnaires), None)
