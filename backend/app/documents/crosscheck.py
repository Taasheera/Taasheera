"""Cross-check the traveler's identity across their documents (CMPS271-50, US 15).

Embassies reject files where the same person is written two ways: "ERIKSSON" on the
passport, "ERICSSON" on the hotel booking. This compares the name, date of birth and
passport number in the application's passport answer with:

- the traveler's latest confirmed passport and their profile (structured fields), and
- the text read from each uploaded supporting document.

Like the completeness check, it is pure and deterministic: no database, no model and no
network, so every rule is testable. Reading free text is a heuristic, so document findings
are warnings the traveler reviews, never proof. A document whose text couldn't be read is
listed as not checked rather than passed.
"""

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Literal

from pydantic import BaseModel

Field = Literal["name", "birth_date", "document_number"]
SourceKind = Literal["passport", "profile", "document"]

FIELD_NAMES: dict[str, str] = {"name": "name", "birth_date": "date of birth", "document_number": "passport number"}
# Below this, the text is too short to say anything about (a photo, a blank scan).
MIN_TEXT = 40
_MONTHS = {name: number for number, names in enumerate(
    [("JAN", "JANUARY"), ("FEB", "FEBRUARY"), ("MAR", "MARCH"), ("APR", "APRIL"), ("MAY",), ("JUN", "JUNE"),
     ("JUL", "JULY"), ("AUG", "AUGUST"), ("SEP", "SEPT", "SEPTEMBER"), ("OCT", "OCTOBER"), ("NOV", "NOVEMBER"),
     ("DEC", "DECEMBER")], start=1) for name in names}
_DATE = re.compile(
    r"(?P<iso>\d{4}-\d{2}-\d{2})"
    r"|(?P<dmy>\d{1,2}[./-]\d{1,2}[./-]\d{4})"
    r"|(?P<text>\d{1,2} (?:[A-Z]{3,9}) \d{4})")
_BIRTH_LABEL = re.compile(r"\b(?:DATE OF BIRTH|BIRTH DATE|BIRTHDATE|DOB|D\.O\.B|BORN(?: ON)?)\b")
_PASSPORT_LABEL = re.compile(r"\b(?:PASSPORT|TRAVEL DOCUMENT)(?: (?:NUMBER|NO|NUM|#))?\.?:? ?#?:? ?(?P<number>[A-Z0-9]{6,10})\b")


class Finding(BaseModel):
    field: Field
    source: SourceKind
    # Where it was found, for the traveler: "Your profile", "Bank statements (statement.pdf)".
    where: str
    expected: str
    found: str
    message: str
    document_id: str | None = None
    requirement: str | None = None


class Report(BaseModel):
    # What everything was compared with: the application's passport answer, or the latest confirmed passport.
    reference: Literal["application", "passport"] | None
    checked: list[str]
    # Documents with no readable text, which could not be checked.
    unreadable: list[str]
    findings: list[Finding]


@dataclass(frozen=True)
class DocumentText:
    id: str
    requirement: str
    label: str
    filename: str
    text: str

    @property
    def where(self) -> str:
        return f"{self.label} ({self.filename})"


def fold(text: str) -> str:
    """Upper case, accents removed, punctuation other than . / - turned into spaces, single spaces."""
    text = unicodedata.normalize("NFKD", str(text))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).upper()
    text = re.sub(r"[^A-Z0-9./#:-]+", " ", text)
    return " ".join(text.split())


def _words(text: str) -> list[str]:
    return re.findall(r"[A-Z]+", fold(text))


def _distance(left: str, right: str) -> int:
    """Levenshtein distance; short strings only."""
    previous = list(range(len(right) + 1))
    for i, a in enumerate(left, start=1):
        current = [i]
        for j, b in enumerate(right, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (a != b)))
        previous = current
    return previous[-1]


def _parse_date(match: re.Match) -> date | None:
    try:
        if match["iso"]:
            return date.fromisoformat(match["iso"])
        if match["dmy"]:
            day, month, year = (int(part) for part in re.split(r"[./-]", match["dmy"]))
            return date(year, month, day)
        day, month, year = match["text"].split(" ")
        return date(int(year), _MONTHS[month], int(day)) if month in _MONTHS else None
    except (ValueError, KeyError):
        return None


def _name(fields: dict) -> str:
    return " ".join(part for part in (fields.get("given_names"), fields.get("surname")) if part)


def _structured(reference: dict, other: dict | None, source: SourceKind, where: str) -> list[Finding]:
    if not other:
        return []
    findings = []
    expected_name, found_name = _name(reference), _name(other)
    if expected_name and found_name and _words(expected_name) != _words(found_name):
        findings.append(Finding(field="name", source=source, where=where, expected=expected_name, found=found_name,
            message=f"{where} has your name as {found_name}, but your application's passport says {expected_name}."))
    for field in ("birth_date", "document_number"):
        expected, found = reference.get(field), other.get(field)
        if expected and found and fold(expected) != fold(found):
            findings.append(Finding(field=field, source=source, where=where, expected=str(expected), found=str(found),
                message=f"{where} has your {FIELD_NAMES[field]} as {found}, but your application's passport says {expected}."))
    return findings


def _document_findings(reference: dict, document: DocumentText) -> list[Finding]:
    text = fold(document.text)
    words = set(re.findall(r"[A-Z]+", text))
    where = document.where
    found_at = dict(source="document", where=where, document_id=document.id, requirement=document.requirement)
    findings: list[Finding] = []

    surnames = _words(reference.get("surname") or "")
    given = _words(reference.get("given_names") or "")
    if surnames and not all(part in words for part in surnames):
        missing = next(part for part in surnames if part not in words)
        # A near miss is almost always the same person spelled differently.
        close = sorted((w for w in words if len(w) >= 3 and abs(len(w) - len(missing)) <= 2
                        and _distance(w, missing) <= max(1, len(missing) // 4)), key=lambda w: _distance(w, missing))
        if close:
            findings.append(Finding(field="name", expected=missing, found=close[0], **found_at, message=(
                f"{where} spells your surname {close[0]}; your passport says {missing}. "
                "Embassies expect every document to match the passport exactly.")))
        else:
            findings.append(Finding(field="name", expected=" ".join(surnames), found="", **found_at, message=(
                f"We couldn't find your surname ({' '.join(surnames)}) on {where}. "
                "Check that the document is in your name, or that a letter about you names you as on your passport.")))
    elif given and given[0] not in words:
        close = sorted((w for w in words if len(w) >= 3 and _distance(w, given[0]) <= max(1, len(given[0]) // 4)),
                       key=lambda w: _distance(w, given[0]))
        if close:
            findings.append(Finding(field="name", expected=given[0], found=close[0], **found_at, message=(
                f"{where} spells your first name {close[0]}; your passport says {given[0]}.")))

    number = fold(reference.get("document_number") or "")
    if number:
        for match in _PASSPORT_LABEL.finditer(text):
            candidate = match["number"]
            if any(ch.isdigit() for ch in candidate) and candidate != number:
                findings.append(Finding(field="document_number", expected=number, found=candidate, **found_at, message=(
                    f"{where} gives a passport number of {candidate}; the passport in your application is {number}. "
                    "If you renewed your passport, update the booking or letter to the new number.")))
                break

    birth = reference.get("birth_date")
    if birth:
        expected = date.fromisoformat(str(birth))
        for label in _BIRTH_LABEL.finditer(text):
            match = _DATE.search(text, label.end(), label.end() + 30)
            found = _parse_date(match) if match else None
            if found and found != expected:
                findings.append(Finding(field="birth_date", expected=expected.isoformat(), found=found.isoformat(),
                    **found_at, message=(f"{where} gives your date of birth as {found.isoformat()}; "
                                         f"your passport says {expected.isoformat()}.")))
                break
    return findings


def cross_check(passport_answer: dict | None, *, latest_passport: dict | None = None, profile: dict | None = None,
                documents: list[DocumentText] = ()) -> Report:
    """Every place the traveler's name, date of birth or passport number disagrees with their passport."""
    reference, reference_kind = (passport_answer, "application") if passport_answer else (latest_passport, "passport")
    if not reference:
        return Report(reference=None, checked=[], unreadable=[], findings=[])
    checked, unreadable, findings = [], [], []
    if reference_kind == "application" and latest_passport:
        checked.append("Your confirmed passport")
        findings += _structured(reference, latest_passport, "passport", "Your confirmed passport")
    if profile:
        checked.append("Your profile")
        findings += _structured(reference, profile, "profile", "Your profile")
    for document in documents:
        if len(document.text.strip()) < MIN_TEXT:
            unreadable.append(document.where)
            continue
        checked.append(document.where)
        findings += _document_findings(reference, document)
    return Report(reference=reference_kind, checked=checked, unreadable=unreadable, findings=findings)
