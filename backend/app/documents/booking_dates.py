"""Compare explicitly labeled booking dates against the confirmed travel window."""
import re
from datetime import date

from .crosscheck import DocumentText, Finding

# Only unambiguous dates are read: ISO (2027-03-28) or a written month ("28 March 2027", "Mar 28, 2027",
# "28-Mar-2027"). All-numeric dates such as 05/01/2027 are skipped rather than guessing the day/month order.
_MONTHS = {name: number for number, names in enumerate((
    ("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"), ("may",), ("jun", "june"),
    ("jul", "july"), ("aug", "august"), ("sep", "sept", "september"), ("oct", "october"), ("nov", "november"),
    ("dec", "december")), start=1) for name in names}
_MONTH = r"(?:%s)\.?" % "|".join(sorted(_MONTHS, key=len, reverse=True))
_DATE = (rf"(?P<iso>\d{{4}}-\d{{2}}-\d{{2}})"
         rf"|(?P<dmy>\d{{1,2}}(?:st|nd|rd|th)?[ -]{_MONTH}[ -,]*\d{{4}})"
         rf"|(?P<mdy>{_MONTH} \d{{1,2}}(?:st|nd|rd|th)?,? \d{{4}})")
# Between a label and its date: a separator and, for flights, a short route such as "BEY-CDG".
_GAP = r"\s*[:–-]?\s*(?:[A-Z]{3}\s*[-–>]+\s*[A-Z]{3}\s*[:,]?\s*)?"
_PERIODS = {
    "accommodation_proof": (r"CHECK[ -]?IN(?: DATE)?", r"CHECK[ -]?OUT(?: DATE)?"),
    "travel_insurance": (r"(?:COVERAGE START(?: DATE)?|VALID FROM|START DATE)",
                         r"(?:COVERAGE END(?: DATE)?|VALID UNTIL|VALID TO|END DATE)"),
}
_FLIGHT_ROLES = (r"(?:OUTBOUND(?: FLIGHT| ARRIVAL)?(?: DATE)?|ARRIVAL DATE AT DESTINATION)",
                 r"(?:RETURN(?: FLIGHT| DEPARTURE)?(?: DATE)?|DEPARTURE DATE FROM DESTINATION)")


def _parse(match: re.Match) -> date | None:
    try:
        if match["iso"]:
            return date.fromisoformat(match["iso"])
        text = (match["dmy"] or match["mdy"]).lower().replace(",", " ").replace("-", " ").replace(".", "")
        parts = [re.sub(r"(st|nd|rd|th)$", "", part) for part in text.split()]
        day, month, year = (parts[0], parts[1], parts[2]) if match["dmy"] else (parts[1], parts[0], parts[2])
        return date(int(year), _MONTHS[month], int(day))
    except (ValueError, KeyError, IndexError):
        return None


def _date_after(text: str, label: str) -> date | None:
    """The one date written right after a label; none when it is missing, unreadable or repeated differently."""
    found = {_parse(match) for match in re.finditer(rf"\b{label}{_GAP}(?:{_DATE})", text, re.IGNORECASE)}
    found.discard(None)
    return found.pop() if len(found) == 1 else None


def booking_findings(document: DocumentText, arrival: date, departure: date) -> list[Finding]:
    text = document.text.replace("‑", "-")
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
        elif (start and start < arrival) or (end and end > departure):
            # A hotel night before arrival or after departure means the booking and the trip disagree.
            reason = "shows an accommodation booking that runs outside your travel dates"
    elif document.requirement == "flight_booking":
        start, end = (_date_after(text, label) for label in _FLIGHT_ROLES)
        # An overnight flight may leave the day before it arrives, so allow one day either way.
        if (start and abs((start - arrival).days) > 1) or (end and abs((end - departure).days) > 1):
            reason = "shows flight dates that differ from your travel dates"
    if reason is None:
        return []
    found = " to ".join(value.isoformat() for value in (start, end) if value)
    return [Finding(field="travel_dates", source="document", where=document.where,
                    expected=expected, found=found, document_id=document.id, requirement=document.requirement,
                    message=f"{document.where} {reason}. Compare {found} with your trip ({expected}).")]


# --- Suggesting answers from uploaded bookings ("fill from your documents") ---------------------

# Where each suggested answer can come from, best first: a flight's dates beat a hotel's check-in.
BOOKED_FROM: dict[str, tuple[str, ...]] = {
    "arrival": ("flight_booking", "itinerary", "accommodation_proof"),
    "departure": ("flight_booking", "itinerary", "accommodation_proof"),
    "stay": ("accommodation_proof",),
}
_STAY_NAME = r"(?:HOTEL|PROPERTY|ACCOMMODATION)(?: NAME)?"
_STAY_ADDRESS = r"(?:(?:HOTEL|PROPERTY) )?ADDRESS"


def _line_after(text: str, label: str) -> str | None:
    """The rest of a line that starts with "Label:"; none when missing or repeated differently."""
    found = {" ".join(match["value"].split()).strip(" ,.")
             for match in re.finditer(rf"^[ \t]*{label}[ \t]*:[ \t]*(?P<value>[^\n]{{2,150}})$", text,
                                      re.IGNORECASE | re.MULTILINE)}
    found.discard("")
    return found.pop() if len(found) == 1 else None


def _booked(kind: str, document: DocumentText) -> str | None:
    text = document.text.replace("‑", "-")
    if kind == "stay":
        name, address = _line_after(text, _STAY_NAME), _line_after(text, _STAY_ADDRESS)
        return ", ".join(part for part in (name, address) if part) or None
    start_label, end_label = (_FLIGHT_ROLES if document.requirement != "accommodation_proof"
                              else _PERIODS["accommodation_proof"])
    found = _date_after(text, start_label if kind == "arrival" else end_label)
    return found.isoformat() if found else None


def booked_value(kind: str, documents: list[DocumentText]) -> tuple[str, DocumentText] | None:
    """The answer the traveler's own bookings give for the trip's arrival, departure or stay.

    Only explicitly labeled values are read, and the first kind of document that gives one
    wins. Two documents of that kind that disagree give nothing rather than a guess. The
    traveler still confirms the suggestion, and the engine checks it like any answer.
    """
    for requirement in BOOKED_FROM[kind]:
        found = {(value, document.id): document for document in documents
                 if document.requirement == requirement and (value := _booked(kind, document))}
        values = {value for value, _ in found}
        if len(values) == 1:
            return values.pop(), next(iter(found.values()))
        if values:
            return None
    return None
