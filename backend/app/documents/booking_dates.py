"""Compare explicitly labeled booking dates against the confirmed travel window."""
import re
from datetime import date

from .crosscheck import DocumentText, Finding

# Date order is unambiguous. Avoid guessing which month comes first in a numeric date.
_ISO_DATE = r"(\d{4}-\d{2}-\d{2})"
_PERIODS = {
    "accommodation_proof": (r"CHECK[ -]?IN(?: DATE)?", r"CHECK[ -]?OUT(?: DATE)?"),
    "travel_insurance": (r"(?:COVERAGE START(?: DATE)?|VALID FROM)", r"(?:COVERAGE END(?: DATE)?|VALID UNTIL)"),
}
_FLIGHT_ROLES = (r"(?:OUTBOUND ARRIVAL(?: DATE)?|ARRIVAL DATE AT DESTINATION)",
                 r"(?:RETURN DEPARTURE(?: DATE)?|DEPARTURE DATE FROM DESTINATION)")


def _date_after(text: str, label: str) -> date | None:
    matches = set(re.findall(rf"\b{label}\s*[:–-]?\s*{_ISO_DATE}\b", text, re.IGNORECASE))
    if len(matches) != 1:
        return None
    try:
        return date.fromisoformat(matches.pop())
    except ValueError:
        return None


def booking_findings(document: DocumentText, arrival: date, departure: date) -> list[Finding]:
    text = document.text.replace("\u2011", "-")
    expected = f"{arrival.isoformat()} to {departure.isoformat()}"
    reason = None
    if document.requirement in _PERIODS:
        start_label, end_label = _PERIODS[document.requirement]
        start, end = _date_after(text, start_label), _date_after(text, end_label)
        if start and end and end < start:
            reason = "shows an end date before its start date"
        elif document.requirement == "travel_insurance":
            if (start and start > arrival) or (end and end < departure):
                reason = "shows insurance coverage that may leave part of your trip uncovered"
        elif start and end and (start > departure or end < arrival):
            reason = "shows an accommodation booking outside your travel dates"
    elif document.requirement == "flight_booking":
        start, end = (_date_after(text, label) for label in _FLIGHT_ROLES)
        if (start and start != arrival) or (end and end != departure):
            reason = "shows flight dates that differ from your travel dates"
    if reason is None:
        return []
    found = " to ".join(value.isoformat() for value in (start, end) if value)
    return [Finding(field="travel_dates", source="document", where=document.where,
                    expected=expected, found=found, document_id=document.id, requirement=document.requirement,
                    message=f"{document.where} {reason}. Compare {found} with your trip ({expected}).")]
