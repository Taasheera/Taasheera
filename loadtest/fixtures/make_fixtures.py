"""Builds the synthetic load-test fixtures (no real person's data). Run: python loadtest/fixtures/make_fixtures.py"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

here = Path(__file__).parent
image = Image.new("RGB", (1200, 800), (236, 240, 232))
draw = ImageDraw.Draw(image)
try:
    font = ImageFont.truetype("DejaVuSansMono.ttf", 34)
    big = ImageFont.truetype("DejaVuSans-Bold.ttf", 64)
except OSError:
    try:
        font, big = ImageFont.truetype("consola.ttf", 34), ImageFont.truetype("arialbd.ttf", 64)
    except OSError:
        font = big = ImageFont.load_default()
draw.rectangle((40, 40, 1160, 760), outline=(60, 60, 60), width=4)
draw.text((420, 70), "SPECIMEN", fill=(180, 30, 30), font=big)
draw.rectangle((70, 180, 350, 520), fill=(200, 205, 200))
for row, text in enumerate(["Surname: SPECIMEN", "Given names: LOAD TEST", "Nationality: UTOPIA",
                            "Date of birth: 01 JAN 1990", "Expiry: 01 JAN 2034"]):
    draw.text((400, 200 + row * 55), text, fill=(30, 30, 30), font=font)
# ICAO 9303 specimen-style MRZ for the fictional state Utopia (UTO), as in ICAO's own examples.
draw.text((70, 600), "P<UTOSPECIMEN<<LOAD<TEST<<<<<<<<<<<<<<<<<<<", fill=(0, 0, 0), font=font)
draw.text((70, 660), "L898902C36UTO9001018X3401018<<<<<<<<<<<<<<06", fill=(0, 0, 0), font=font)
image.save(here / "specimen-passport.jpg", "JPEG", quality=80)

text = "Synthetic booking for load testing. Arrival 2027-03-01. Departure 2027-03-10. Hotel Specimen, Paris."
stream = f"BT /F1 12 Tf 50 750 Td ({text}) Tj ET".encode()
objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
           b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
           b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
           b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
pdf, offsets = bytearray(b"%PDF-1.4\n"), []
for number, body in enumerate(objects, 1):
    offsets.append(len(pdf))
    pdf += b"%d 0 obj\n" % number + body + b"\nendobj\n"
xref = len(pdf)
pdf += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
pdf += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
pdf += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
(here / "synthetic-booking.pdf").write_bytes(bytes(pdf))
print("fixtures written")
