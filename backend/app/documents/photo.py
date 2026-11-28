import json
from functools import cache
from io import BytesIO
from pathlib import Path
from statistics import fmean, pstdev
from typing import Literal

from PIL import Image, ImageChops, ImageStat, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field

from ..applications import completeness, flow
from ..applications.passport_rules import Source
from ..models import Application, SupportingDocument
from ..passport.quality import centre_sample, edge_strength
from .storage import DocumentStorage

SPECS_FILE = Path(__file__).resolve().parents[1] / "applications" / "photo_specs.json"
SHAPE_TOLERANCE = 0.03
MIN_SHARPNESS = 4.0
MIN_COLOUR_SPREAD = 4.0
BACKGROUND_FLOOR = {"light": 165, "white": 200}
MAX_BACKGROUND_SPREAD = 32
NOTICE = ("These checks look only at the file: its size, shape, colour, background and sharpness. They don't "
          "recognise faces or check your pose or expression, so compare the photo with the official rules too. "
          "This is not legal or immigration advice.")

CheckId = Literal["file_type", "size", "max_size", "shape", "colour", "background", "sharpness", "file_size"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PhotoSpec(_Strict):
    questionnaire_id: str
    requirement: str
    label: str = Field(min_length=1, max_length=120)
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    shape: str = Field(min_length=1, max_length=40)
    min_pixels: tuple[int, int]
    max_pixels: tuple[int, int] | None = None
    min_kb: int | None = Field(default=None, ge=1)
    max_kb: int | None = Field(default=None, ge=1)
    colour: bool
    background: Literal["light", "white"] | None
    source: Source


class SpecsFile(_Strict):
    checked_on: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    specs: list[PhotoSpec]


class PhotoCheck(BaseModel):
    id: CheckId
    passed: bool
    message: str
    guidance: str | None = None


class PhotoReport(BaseModel):
    spec: str
    passed: bool
    checks: list[PhotoCheck]
    source: Source
    notice: str = NOTICE


@cache
def load_specs() -> SpecsFile:
    data = SpecsFile.model_validate(json.loads(SPECS_FILE.read_text(encoding="utf-8")))
    for spec in data.specs:
        documents = {document.id for document in flow.load_questionnaire(spec.questionnaire_id).documents}
        if spec.requirement not in documents:
            raise ValueError(f"{spec.questionnaire_id} has no {spec.requirement!r} document for its photo spec.")
    return data


def photo_spec(questionnaire_id: str, requirement: str) -> PhotoSpec | None:
    return next((spec for spec in load_specs().specs
                 if spec.questionnaire_id == questionnaire_id and spec.requirement == requirement), None)


def _check(id: CheckId, passed: bool, message: str, guidance: str) -> PhotoCheck:
    return PhotoCheck(id=id, passed=passed, message=message, guidance=None if passed else guidance)


def _colour_spread(image: Image.Image) -> float:
    sample = image.copy()
    sample.thumbnail((300, 300))
    red, green, blue = sample.split()
    spread = ImageChops.lighter(ImageChops.lighter(ImageChops.difference(red, green), ImageChops.difference(green, blue)),
                                ImageChops.difference(red, blue))
    return ImageStat.Stat(spread).mean[0]


def _border(image: Image.Image) -> tuple[float, float]:
    grey = image.convert("L")
    grey.thumbnail((300, 300))
    width, height = grey.size
    band = max(2, round(min(width, height) * 0.06))
    lower = max(band + 1, height * 7 // 10)
    strips = (grey.crop((0, 0, width, band)), grey.crop((0, band, band, lower)), grey.crop((width - band, band, width, lower)))
    pixels = [value for strip in strips for value in strip.getdata()]
    return fmean(pixels), pstdev(pixels)


def _file_size(content: bytes, spec: PhotoSpec) -> PhotoCheck:
    size = len(content) / 1024
    low, high = spec.min_kb, spec.max_kb
    wanted = (f"{low} to {high} KB" if low and high else f"at least {low} KB" if low else f"at most {high} KB")
    return _check("file_size", (not low or size >= low) and (not high or size <= high),
                  f"The file is {round(size)} KB; the official form takes {wanted}.",
                  f"Save the photo again at {wanted} before you upload it to the official form.")


def check_photo(content: bytes, content_type: str, spec: PhotoSpec) -> PhotoReport:
    def report(checks: list[PhotoCheck]) -> PhotoReport:
        return PhotoReport(spec=spec.label, passed=all(check.passed for check in checks), checks=checks,
                           source=spec.source)

    unreadable = _check("file_type", False, "This photo was uploaded as a PDF, so it can't be checked.",
                        "Upload the photo itself as a JPEG, PNG or WebP image.")
    if content_type != "image/jpeg":
        return report([unreadable])
    try:
        with Image.open(BytesIO(content)) as opened:
            image = opened.convert("RGB")
    except (UnidentifiedImageError, OSError):
        return report([unreadable.model_copy(update={"message": "This photo couldn't be opened as an image."})])

    width, height = image.size
    min_width, min_height = spec.min_pixels
    checks = [_check("size", width >= min_width and height >= min_height,
                     f"It is {width} x {height} pixels; at least {min_width} x {min_height} are needed.",
                     "Retake it at full resolution, or scan the printed photo at 600 dpi.")]
    if spec.max_pixels:
        max_width, max_height = spec.max_pixels
        checks.append(_check("max_size", width <= max_width and height <= max_height,
                             f"It is {width} x {height} pixels; the official form takes at most {max_width} x {max_height}.",
                             f"Resize it to at most {max_width} x {max_height} pixels before you upload it to the official form."))
    ratio, target = width / height, spec.width / spec.height
    checks.append(_check("shape", abs(ratio / target - 1) <= SHAPE_TOLERANCE,
                         f"Its width to height is {ratio:.2f}; a {spec.shape} photo is {target:.2f}.",
                         f"Crop it to {spec.shape} proportions, with your head centred and nothing cut off."))
    if spec.colour:
        coloured = _colour_spread(image) >= MIN_COLOUR_SPREAD
        checks.append(_check("colour", coloured, "It is in colour." if coloured else "It looks black and white.",
                             "Use a recent colour photo."))
    if spec.background:
        brightness, spread = _border(image)
        light, plain = brightness >= BACKGROUND_FLOOR[spec.background], spread <= MAX_BACKGROUND_SPREAD
        wall = "white" if spec.background == "white" else "white or light-coloured"
        if light and plain:
            found = f"The background looks plain and {'white' if spec.background == 'white' else 'light'}."
        elif not light:
            found = "The background looks too dark." if plain else "The background looks too dark and uneven."
        else:
            found = "The background looks busy or uneven."
        checks.append(_check("background", light and plain, found,
                             f"Stand in front of a plain {wall} wall, in even light with no shadows behind you."))
    sharp = edge_strength(centre_sample(image)) >= MIN_SHARPNESS
    checks.append(_check("sharpness", sharp, "It looks sharp." if sharp else "It looks blurry or out of focus.",
                         "Hold the camera steady, focus on your face and use good, even light."))
    if spec.min_kb or spec.max_kb:
        checks.append(_file_size(content, spec))
    return report(checks)


def check_document_photo(storage: DocumentStorage, document: SupportingDocument, spec: PhotoSpec, *,
                         purpose: str) -> PhotoReport | None:
    if document.content_type != "image/jpeg":
        return check_photo(b"", document.content_type, spec)
    content = storage.load(document.traveler_id, document.file_key, "jpg", purpose=purpose)
    return None if content is None else check_photo(content, document.content_type, spec)


def photo_issues(storage: DocumentStorage, application: Application, answers: flow.Answers,
                 documents: list[SupportingDocument]) -> list[completeness.Issue]:
    questionnaire = flow.load_questionnaire(application.questionnaire_id)
    asked = {requirement.id: requirement.label for requirement in flow.required_documents(questionnaire, answers)}
    issues = []
    for document in documents:
        spec = photo_spec(questionnaire.id, document.requirement) if document.requirement in asked else None
        report = check_document_photo(storage, document, spec, purpose="document_check") if spec else None
        if report is None or report.passed:
            continue
        found = " ".join(check.message for check in report.checks if not check.passed)
        issues.append(completeness.Issue(
            kind="inconsistent", severity="warning", document_id=document.requirement, source=spec.source.url,
            message=f"{asked[document.requirement]} ({document.filename}) may not meet the {spec.label} rules. {found}"))
    return issues
