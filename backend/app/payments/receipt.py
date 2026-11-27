"""The receipt for a paid payment, as a one-page PDF (CMPS271-109).

Written directly with the PDF standard fonts, so the text stays selectable and no library is needed.
It shows the receipt number, the date, the lines paid, the total and that the payment was simulated.
A payment holds no card details, so a receipt can't show any.
"""

import textwrap
from datetime import datetime, timezone

from ..models import Payment
from .quote import MINOR_UNITS

WIDTH, HEIGHT = 595, 842
LEFT, RIGHT = 56, 539
SIMULATED = "Simulated payment — no card was charged"
LINE_NAMES = {"service_fee": "Service fee", "embassy_fee": "Embassy fee", "discount": "Discount"}


def receipt_number(payment: Payment) -> str:
    return f"TA-{payment.id[:10].upper()}"


def format_money(amount_minor: int, currency: str) -> str:
    sign = "-" if amount_minor < 0 else ""
    whole, cents = divmod(abs(amount_minor), MINOR_UNITS)
    return f"{sign}{currency} {whole:,}.{cents:02d}"


def _text(value: str) -> str:
    data = value.encode("cp1252", errors="replace")
    return data.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)").decode("latin-1")


class _Page:
    def __init__(self):
        self.ops: list[str] = []
        self.y = HEIGHT - 72

    def text(self, value: str, *, font: str = "F1", size: float = 11, x: float = LEFT, colour: str = "0 0 0"):
        self.ops.append(f"{colour} rg BT /{font} {size} Tf {x:.1f} {self.y:.1f} Td ({_text(value)}) Tj ET")

    def amount(self, value: str, size: float = 11, font: str = "F3"):
        self.text(value, font=font, size=size, x=RIGHT - len(value) * size * 0.6)

    def rule(self):
        self.ops.append(f"0.75 G 0.8 w {LEFT} {self.y:.1f} m {RIGHT} {self.y:.1f} l S")

    def down(self, points: float):
        self.y -= points


def _pdf(content: str, title: str) -> bytes:
    stream = content.encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {WIDTH} {HEIGHT}] /Contents 7 0 R "
         "/Resources << /Font << /F1 4 0 R /F2 5 0 R /F3 6 0 R >> >> >>").encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier /Encoding /WinAnsiEncoding >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        f"<< /Title ({_text(title)}) /Producer (Ta'asheera) >>".encode("latin-1"),
    ]
    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(output)
    output += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    output += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    output += b"trailer\n<< /Size %d /Root 1 0 R /Info 8 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(output)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def build_receipt(payment: Payment, application_title: str, billed_to: str) -> bytes:
    number = receipt_number(payment)
    page = _Page()
    page.text("Ta'asheera", font="F2", size=22)
    page.down(30)
    page.text("RECEIPT", font="F2", size=13, colour="0.35 0.35 0.35")
    page.down(26)
    page.text(SIMULATED, font="F2", size=12, colour="0.7 0.1 0.1")
    page.down(34)
    paid_at = _utc(payment.paid_at or payment.created_at)
    for label, value in (("Receipt number", number), ("Date paid", paid_at.strftime("%d %B %Y, %H:%M UTC")),
                         ("Application", application_title), ("Billed to", billed_to),
                         ("Payment method", "Simulated (test mode). No card details were collected."),
                         ("Reference", payment.provider_reference or "")):
        page.text(f"{label}:", font="F2", size=10.5)
        page.text(value, size=10.5, x=LEFT + 110)
        page.down(17)
    page.down(16)
    page.text("Item", font="F2")
    page.amount("Amount", font="F2")
    page.down(8)
    page.rule()
    page.down(18)
    for line in payment.line_items:
        wrapped = textwrap.wrap(line["label"], 62) or [LINE_NAMES.get(line["kind"], "")]
        page.text(wrapped[0])
        page.amount(format_money(line["amount_minor"], payment.currency))
        for more in wrapped[1:]:
            page.down(15)
            page.text(more)
        page.down(20)
    if not any(line["kind"] == "discount" for line in payment.line_items):
        page.text("Discount")
        page.amount("None")
        page.down(20)
    page.rule()
    page.down(20)
    page.text("Total paid", font="F2", size=12)
    page.amount(format_money(payment.amount_minor, payment.currency), size=12)
    page.down(48)
    notes = ("This receipt is for a simulated payment made in test mode: no money was taken and no card was "
             "charged. Keep it with your application records.",
             "Ta'asheera doesn't give legal or immigration advice.")
    for note in notes:
        for part in textwrap.wrap(note, 95):
            page.text(part, size=9.5, colour="0.3 0.3 0.3")
            page.down(14)
        page.down(6)
    return _pdf("\n".join(page.ops), f"Ta'asheera receipt {number}")
