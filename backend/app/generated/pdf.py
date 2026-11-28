"""Print a generated document as an A4 PDF, in the same way the packet draws its cover page."""
from io import BytesIO

from PIL import Image, ImageDraw

from ..documents.packet import MARGIN, PAGE, _font, _wrap

LINE = 38
PARAGRAPH = 14


def render_pdf(title: str, body: str) -> bytes:
    """One or more A4 pages of plain text. Blank lines in `body` separate paragraphs."""
    font = _font(27)
    pages: list[Image.Image] = []
    image = draw = None
    y = 0
    width = PAGE[0] - 2 * MARGIN

    def new_page():
        nonlocal image, draw, y
        image = Image.new("RGB", PAGE, "white")
        draw = ImageDraw.Draw(image)
        pages.append(image)
        y = MARGIN

    new_page()
    for paragraph in body.split("\n"):
        lines = _wrap(draw, paragraph, font, width) if paragraph.strip() else []
        if not lines:
            y += PARAGRAPH + LINE // 2
            continue
        for line in lines:
            if y + LINE > PAGE[1] - MARGIN:
                new_page()
            draw.text((MARGIN, y), line, font=font, fill="black")
            y += LINE
    output = BytesIO()
    pages[0].save(output, format="PDF", resolution=150, save_all=True, append_images=pages[1:],
                  title=title, producer="Ta'asheera")
    return output.getvalue()
