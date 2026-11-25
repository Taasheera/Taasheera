"""The print-ready packet (CMPS271-71): one PDF with a cover checklist and every uploaded document.

The cover lists what the embassy asks for, in the questionnaire's order, with the page each document
starts on or "Not uploaded yet". Documents follow in that order: PDFs as uploaded, images (already
re-encoded JPEGs) as one page each. Nothing here is sent anywhere; the traveler prints it.
"""
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader, PdfWriter
from pypdf.errors import PdfReadError

# A4 at 150 dpi.
PAGE = (1240, 1754)
MARGIN = 110
_FONTS = ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf",
          "/System/Library/Fonts/Helvetica.ttc", "C:/Windows/Fonts/arial.ttf")
_BOLD = ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
         "C:/Windows/Fonts/arialbd.ttf")


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    for path in (_BOLD if bold else ()) + _FONTS:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size)


@dataclass(frozen=True)
class PacketItem:
    label: str
    required: bool
    # (filename, content type, bytes) for each uploaded file, oldest first.
    files: list[tuple[str, str, bytes]]


def _pages(name: str, content_type: str, content: bytes) -> PdfReader | None:
    """The document as PDF pages, or None when it can't be added (for example an encrypted PDF)."""
    try:
        if content_type == "application/pdf":
            reader = PdfReader(BytesIO(content))
            if reader.is_encrypted:
                return None
            _ = len(reader.pages)
            return reader
        image = Image.open(BytesIO(content)).convert("RGB")
        output = BytesIO()
        image.save(output, format="PDF", resolution=150)
        return PdfReader(BytesIO(output.getvalue()))
    except (PdfReadError, OSError, ValueError):
        return None


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> list[str]:
    lines, line = [], ""
    for word in text.split():
        candidate = f"{line} {word}".strip()
        if draw.textlength(candidate, font=font) <= width or not line:
            line = candidate
        else:
            lines.append(line)
            line = word
    return lines + ([line] if line else [])


def _cover(title: str, traveler: str, trip: str, rows: list[tuple[str, str, bool]], notes: list[str]) -> bytes:
    image = Image.new("RGB", PAGE, "white")
    draw = ImageDraw.Draw(image)
    width = PAGE[0] - 2 * MARGIN
    y = MARGIN
    small, body, bold, heading = _font(24), _font(28), _font(28, True), _font(46, True)
    draw.text((MARGIN, y), "APPLICATION PACKET", font=small, fill=(90, 90, 90))
    y += 44
    for line in _wrap(draw, title, heading, width):
        draw.text((MARGIN, y), line, font=heading, fill="black")
        y += 60
    for line in (traveler, trip):
        if line:
            draw.text((MARGIN, y), line, font=body, fill=(40, 40, 40))
            y += 42
    y += 30
    draw.line((MARGIN, y, PAGE[0] - MARGIN, y), fill=(180, 180, 180), width=2)
    y += 30
    draw.text((MARGIN, y), "Documents, in this order", font=bold, fill="black")
    y += 56
    for label, where, missing in rows:
        mark = "[ ]" if missing else "[-]" if where == "Not included" else "[x]"
        colour = (170, 40, 30) if missing else "black"
        draw.text((MARGIN, y), mark, font=body, fill=colour)
        label_lines = _wrap(draw, label, body, width - 380)
        for index, line in enumerate(label_lines):
            draw.text((MARGIN + 70, y + index * 38), line, font=body, fill="black")
        draw.text((PAGE[0] - MARGIN - draw.textlength(where, font=body), y), where, font=body, fill=colour)
        y += 38 * len(label_lines) + 18
    y += 30
    for note in notes:
        for line in _wrap(draw, note, small, width):
            draw.text((MARGIN, y), line, font=small, fill=(70, 70, 70))
            y += 34
        y += 10
    output = BytesIO()
    image.save(output, format="PDF", resolution=150)
    return output.getvalue()


def build_packet(title: str, traveler: str, trip: str, items: list[PacketItem]) -> bytes:
    """Cover page, then each document. Page numbers on the cover count the cover as page 1."""
    readers: list[tuple[PacketItem, str, PdfReader | None]] = []
    rows: list[tuple[str, str, bool]] = []
    page = 2
    for item in items:
        if not item.files:
            rows.append((item.label if item.required else f"{item.label} (optional)",
                         "Not uploaded yet" if item.required else "Not included", item.required))
            continue
        for name, content_type, content in item.files:
            reader = _pages(name, content_type, content)
            readers.append((item, name, reader))
            label = f"{item.label}: {name}"
            if reader is None:
                rows.append((label, "Print separately", True))
            else:
                rows.append((label, f"Page {page}", False))
                page += len(reader.pages)
    notes = ["Bring your passport and any original documents the embassy asks to see. Sign the application "
             "form yourself.",
             "Prepared with Ta'asheera from your own uploads. Check the embassy's current list: it may ask for "
             "more. Ta'asheera doesn't give legal or immigration advice."]
    writer = PdfWriter()
    writer.append(PdfReader(BytesIO(_cover(title, traveler, trip, rows, notes))))
    for _, _, reader in readers:
        if reader is not None:
            writer.append(reader)
    writer.add_metadata({"/Title": f"{title} packet", "/Producer": "Ta'asheera"})
    output = BytesIO()
    writer.write(output)
    return output.getvalue()
