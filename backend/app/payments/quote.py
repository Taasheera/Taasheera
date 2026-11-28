"""The price of submitting one application, worked out on the server (CMPS271-106).

A quote is in one currency (ISO 4217) with amounts in its minor unit. The embassy fee is the first
fee in the destination's visa guide (`applications/visa_guides.json`, the standard fee for one
adult) and sets the currency; the service fee is charged in that currency. An embassy fee that isn't
a fixed amount ("About ¥15,000", "Varies by passport") or is in a currency not in SERVICE_FEE is
not charged here: the quote names it as paid at the embassy and is in DEFAULT_CURRENCY.
"""

import re
from typing import Literal

from pydantic import BaseModel

from ..applications.visa_guides import Link, load_guides
from ..models import Application

SERVICE_FEE = {"USD": 2900, "EUR": 2700, "GBP": 2300, "CAD": 3900}
DEFAULT_CURRENCY = "USD"
SERVICE_FEE_LABEL = "Ta'asheera service fee"
MINOR_UNITS = 100
PREFIXES = (("US$", "USD"), ("CAD", "CAD"), ("€", "EUR"), ("£", "GBP"), ("$", "USD"))
AMOUNT = re.compile(r"(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{2}))?")


class QuoteLine(BaseModel):
    kind: Literal["service_fee", "embassy_fee", "discount"]
    label: str
    amount_minor: int


class Quote(BaseModel):
    currency: str
    lines: list[QuoteLine]
    total_minor: int
    paid_at_embassy: str | None
    fee_source: Link
    checked_on: str


def parse_fee(text: str) -> tuple[str, int] | None:
    """A fixed fee such as "€90" or "US$44.40" as (currency, minor units); None for anything else."""
    for prefix, currency in PREFIXES:
        if text.startswith(prefix):
            match = AMOUNT.fullmatch(text[len(prefix):].strip())
            if match is None:
                return None
            whole, cents = match.groups()
            return currency, int(whole.replace(",", "")) * MINOR_UNITS + int(cents or 0)
    return None


def discounts(application: Application, currency: str, subtotal_minor: int) -> list[QuoteLine]:
    """Discount lines, as negative amounts. None apply yet: the multi-traveler discount (CMPS271-111) goes here."""
    return []


def build_quote(application: Application) -> Quote:
    guide = {guide.id: guide for guide in load_guides()}[application.questionnaire_id]
    fee = guide.fees.items[0]
    parsed = parse_fee(fee.amount)
    if parsed is not None and parsed[0] in SERVICE_FEE:
        currency, embassy_minor = parsed
        lines = [QuoteLine(kind="service_fee", label=SERVICE_FEE_LABEL, amount_minor=SERVICE_FEE[currency]),
                 QuoteLine(kind="embassy_fee", label=f"Embassy fee: {fee.label}", amount_minor=embassy_minor)]
        at_embassy = None
    else:
        currency = DEFAULT_CURRENCY
        lines = [QuoteLine(kind="service_fee", label=SERVICE_FEE_LABEL, amount_minor=SERVICE_FEE[currency])]
        at_embassy = f"{fee.label}: {fee.amount}"
    lines += discounts(application, currency, sum(line.amount_minor for line in lines))
    return Quote(currency=currency, lines=lines, total_minor=max(0, sum(line.amount_minor for line in lines)),
                 paid_at_embassy=at_embassy, fee_source=guide.fees.source, checked_on=guide.checked_on)
