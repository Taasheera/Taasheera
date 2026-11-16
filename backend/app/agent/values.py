"""What the agent types into each form field, worked out from the confirmed application (CMPS271-67).

Pure: the same answers always give the same values, so the mapping is tested without a browser.
Values only ever come from the traveler's own confirmed answers and account; nothing is guessed.
A field with no value here is left empty, and the filler stops if the form requires it.
"""

from datetime import date

from .sites import FormSite

# ICAO codes that differ from the ISO 3166 codes the embassy forms use.
_ISO = {"D": "DEU", "RKS": "XKX"}
_OCCUPATIONS = {"employed": "Employee", "self_employed": "Self-employed", "student": "Student",
                "retired": "Retired", "unemployed": "Unemployed", "other": "Other"}


def _country(code: str | None) -> str | None:
    return _ISO.get(code, code) if code else None


def _date(value: str | None, site: FormSite) -> str | None:
    return date.fromisoformat(value).strftime(site.date_format) if value else None


def _address(address: dict | None) -> str | None:
    if not address:
        return None
    lines = [address.get("line1"), address.get("line2"),
             " ".join(part for part in (address.get("postal_code"), address.get("city")) if part),
             address.get("region"), address.get("country")]
    return "\n".join(line for line in lines if line)


def schengen_values(answers: dict, *, email: str, site: FormSite) -> dict[str, str]:
    """Form values for the harmonised Schengen form, by field key. Keys with no value are left out."""
    passport = answers.get("passport") or {}
    occupation = answers.get("occupation")
    sponsored = answers.get("funding") == "sponsor"
    values = {
        "surname": passport.get("surname"),
        "surname_at_birth": answers.get("surname_at_birth") or None,
        "first_names": passport.get("given_names"),
        "date_of_birth": _date(passport.get("birth_date"), site),
        "place_of_birth": answers.get("place_of_birth"),
        "country_of_birth": _country(answers.get("country_of_birth")),
        "nationality": _country(passport.get("nationality")),
        "sex": passport.get("sex"),
        "civil_status": answers.get("marital_status"),
        "document_type": "ordinary",
        "document_number": passport.get("document_number"),
        "issue_date": _date(answers.get("passport_issue_date"), site),
        "valid_until": _date(passport.get("expiry_date"), site),
        "issued_by": _country(passport.get("issuing_country")),
        "home_address": _address(answers.get("home_address")),
        "email": email,
        "phone": answers.get("phone"),
        "occupation": _OCCUPATIONS.get(occupation) if occupation else None,
        "employer": answers.get("employer") or answers.get("school"),
        "purpose": answers.get("purpose"),
        "main_destination": _country(answers.get("main_destination")),
        "entries": answers.get("entries"),
        "arrival_date": _date(answers.get("arrival_date"), site),
        "departure_date": _date(answers.get("departure_date"), site),
        "fingerprints": None if answers.get("previous_fingerprints") is None
        else ("yes" if answers["previous_fingerprints"] else "no"),
        "fingerprints_date": _date(answers.get("fingerprints_date"), site),
        "accommodation": answers.get("host_details") or answers.get("accommodation_details"),
        "cost_covered_by": None if answers.get("funding") is None else ("sponsor" if sponsored else "applicant"),
        "sponsor": answers.get("sponsor_details") if sponsored else None,
    }
    return {key: str(value) for key, value in values.items() if value not in (None, "")}


VALUE_BUILDERS = {"schengen-harmonised": schengen_values}
