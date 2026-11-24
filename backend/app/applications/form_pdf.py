"""Fill the Commission's static Schengen form from a versioned field map."""
from datetime import date
from functools import lru_cache
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, NameObject, NumberObject, RectangleObject, TextStringObject

from . import flow

DIRECTORY = Path(__file__).with_name("forms")


class FieldMap(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page: int
    key: str
    label: str
    sources: list[str]
    box: tuple[float, float, float, float] | None = None
    marks: dict[str, tuple[float, float]] | None = None
    formatter: Literal["answer", "date", "address"] = "answer"


class FormMap(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str
    source: str
    sha256: str
    manual: list[str]
    fields: list[FieldMap]


class FormValue(BaseModel):
    field: str
    source: str
    value: str
    source_value: str
    manual: bool = False


@lru_cache(maxsize=None)
def load_form(questionnaire_id: str) -> tuple[FormMap, bytes] | None:
    path = DIRECTORY / f"{questionnaire_id}.json"
    if not path.is_file():
        return None
    form = FormMap.model_validate_json(path.read_bytes())
    pdf = path.with_suffix(".pdf").read_bytes()
    if sha256(pdf).hexdigest() != form.sha256:
        raise ValueError("The embassy PDF does not match its field map.")
    keys = set()
    questionnaire = flow.load_questionnaire(questionnaire_id)
    pages = PdfReader(BytesIO(pdf)).pages
    for field in form.fields:
        if field.key in keys or not 1 <= field.page <= len(pages) or bool(field.box) == bool(field.marks):
            raise ValueError("Invalid embassy PDF field map.")
        keys.add(field.key)
        if not field.sources or any(source != "account_email" and not questionnaire.question(source.split(".")[0])
                                    for source in field.sources):
            raise ValueError("Unknown question in embassy PDF field map.")
    return form, pdf


def build_values(form: FormMap, questionnaire: flow.Questionnaire, answers: flow.Answers,
                 account_email: str) -> list[tuple[FieldMap, FormValue, str]]:
    values = []
    for field in form.fields:
        for path in field.sources:
            key, _, subfield = path.partition(".")
            question = questionnaire.question(key) if key != "account_email" else None
            raw = answers.get(key) if question else account_email
            if subfield:
                raw = raw.get(subfield) if isinstance(raw, dict) else None
            if raw is None or raw == "":
                continue
            answer = flow.display_value(question, raw) if question and not subfield else str(raw)
            if field.formatter == "address":
                answer = ", ".join(str(raw[part]) for part in ("line1", "line2", "city", "region", "postal_code", "country")
                                   if raw.get(part))
            if isinstance(raw, bool):
                answer = "Yes" if raw else "No"
            marker = str(raw).lower() if isinstance(raw, bool) else str(raw)
            value = date.fromisoformat(raw).strftime("%d-%m-%Y") if field.formatter == "date" else answer
            value = " ".join(value.split())
            # The Commission template accepts compact Latin-script entries. Longer or unsupported
            # values remain visible in review for the traveler to add separately.
            if field.marks:
                manual = marker not in field.marks
            else:
                try:
                    value.encode("cp1252")
                    estimated_width = len(value) * (4.5 if field.formatter == "date" else 6)
                    manual = estimated_width + 4 > field.box[2] - field.box[0]
                except UnicodeEncodeError:
                    manual = True
            values.append((field, FormValue(field=field.label, source=question.prompt if question else "Sign-in email",
                                           value=value, source_value=answer, manual=manual), marker))
            break
    return values


def render_pdf(template: bytes, values: list[tuple[FieldMap, FormValue, str]]) -> bytes:
    writer = PdfWriter(clone_from=BytesIO(template))
    fields = ArrayObject()
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
        NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
    })
    writer.root_object[NameObject("/AcroForm")] = DictionaryObject({
        NameObject("/Fields"): fields,
        NameObject("/DA"): TextStringObject("/Helv 8 Tf 0 g"),
        NameObject("/DR"): DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/Helv"): font})}),
    })
    fill = {}
    for field, review, marker in values:
        if review.manual:
            continue
        page = writer.pages[field.page - 1]
        height = float(page.mediabox.height)
        box = field.box
        value = review.value
        if field.marks:
            x, y = field.marks[marker]
            box = x - 2, y - 4, x + 8, y + 4
            value = "X"
        left, top, right, bottom = box
        widget = writer.add_annotation(page, DictionaryObject({
            NameObject("/Type"): NameObject("/Annot"),
            NameObject("/Subtype"): NameObject("/Widget"),
            NameObject("/FT"): NameObject("/Tx"),
            NameObject("/T"): TextStringObject(field.key),
            NameObject("/Rect"): RectangleObject((left, height - bottom, right, height - top)),
            NameObject("/F"): NumberObject(4),
        }))
        fields.append(widget.indirect_reference)
        fill[field.key] = (value, "/Helv", 7 if field.marks else 8)
    writer.update_page_form_field_values(None, fill, auto_regenerate=False)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()
