"""The checks a destination's requirements pass, for the packaged files and the back-office alike (CMPS271-123).

A questionnaire and its visa guide facts written in the back-office are published only when they pass what
the packaged files pass: the questionnaire model `load_questionnaire` reads them with, the visa guide model
of visa_guides.json, the official form guide and embassy PDF map that use its questions, and a walk with
ordinary answers that reaches the end, asks for the passport and lists documents (tests/test_application_flow.py
walks every packaged file the same way). Problems name the place in the submitted JSON. Pure: no database.
"""

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from . import flow, form_pdf, portal_guide, visa_guides

MAX_BYTES = 200_000
WORDING = frozenset({"title", "prompt", "help", "label", "message", "source", "destination_label",
                     "destination_description", "visa_label", "visa_description", "version"})
FIXED = ("id", "destination", "visa_type")
SPECIMEN = {
    "surname": "ERIKSSON", "given_names": "ANNA MARIA", "document_number": "L898902C3", "nationality": "UTO",
    "issuing_country": "UTO", "birth_date": "1974-08-12", "sex": "F", "personal_number": "ZE184226B",
}
ADDRESS = {"line1": "1 Example Street", "line2": "", "city": "Beirut", "region": "", "postal_code": "", "country": "LBN"}


class Problem(BaseModel):
    """Where in the submitted JSON: `loc` by keys and list positions (["steps", 1, "questions", 3, "options"]),
    `path` by ids where the items have them (steps[passport].questions[passport_issue_date].options)."""
    part: Literal["questionnaire", "guide"]
    loc: list[str | int]
    path: str
    message: str


class Change(BaseModel):
    part: Literal["questionnaire", "guide"]
    path: str
    kind: Literal["added", "removed", "changed", "moved"]
    before: Any = None
    after: Any = None


@dataclass(frozen=True)
class Checked:
    problems: list[Problem] = field(default_factory=list)
    questionnaire: flow.Questionnaire | None = None
    guide: visa_guides.GuideData | None = None
    checked_on: str | None = None


def packaged_file(questionnaire_id: str) -> dict:
    """The packaged questionnaire as written in its file; KeyError for anything else."""
    flow.packaged_questionnaire(questionnaire_id)
    return json.loads((flow.QUESTIONNAIRE_DIR / f"{questionnaire_id}.json").read_text(encoding="utf-8"))


def packaged_guide(questionnaire_id: str) -> tuple[visa_guides.GuideData, str]:
    """The packaged visa guide facts for one questionnaire and the date they were checked."""
    data = visa_guides.GuidesFile.model_validate(json.loads(visa_guides.GUIDES_FILE.read_text(encoding="utf-8")))
    guide = next((guide for guide in data.guides if guide.questionnaire_id == questionnaire_id), None)
    if guide is None:
        raise KeyError(questionnaire_id)
    return guide, data.checked_on


def questionnaire_json(questionnaire: flow.Questionnaire) -> dict:
    return questionnaire.model_dump(mode="json", by_alias=True, exclude_defaults=True)


def guide_json(guide: visa_guides.GuideData, checked_on: str) -> dict:
    return {"checked_on": checked_on, **guide.model_dump(mode="json", exclude={"questionnaire_id"})}


def structure(questionnaire: flow.Questionnaire) -> Any:
    """The questionnaire without its WORDING: what decides the questions asked and the answers accepted."""
    def strip(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: strip(item) for key, item in value.items() if key not in WORDING}
        if isinstance(value, list):
            return [strip(item) for item in value]
        return value
    return strip(questionnaire_json(questionnaire))


def _path(data: Any, loc) -> str:
    parts, node = [], data
    for key in loc:
        if isinstance(key, int):
            item = node[key] if isinstance(node, list) and -len(node) <= key < len(node) else None
            name = (item.get("id") or item.get("value")) if isinstance(item, dict) else None
            parts.append(f"[{name}]" if isinstance(name, str) and name else f"[{key}]")
            node = item
        else:
            parts.append(f".{key}" if parts else str(key))
            node = node.get(key) if isinstance(node, dict) else None
    return "".join(parts)


def _problem(part, loc, data, message: str) -> Problem:
    return Problem(part=part, loc=list(loc), path=_path(data, loc), message=message)


def _validation_problems(part, error: ValidationError, data, prefix: tuple = ()) -> list[Problem]:
    found = []
    for detail in error.errors():
        loc = detail["loc"][len(prefix):] if detail["loc"][:len(prefix)] == prefix else detail["loc"]
        found.append(_problem(part, loc, data, detail["msg"].removeprefix("Value error, ")))
    return found


def _ordinary_answer(question: flow.Question, given: flow.Answers, today: date) -> Any:
    """An everyday answer that carries on where the question allows it; the ICAO 9303 specimen as the passport."""
    stops = question.stop_if.values if question.stop_if else []
    match question.type:
        case "choice":
            values = [option.value for option in question.options or []]
            return next((value for value in values if value not in stops), values[0] if values else None)
        case "yes_no":
            return next((value for value in (False, True) if value not in stops), False)
        case "date":
            chosen = today + timedelta(days={"arrival_date": 30, "departure_date": 40}.get(question.id, -700))
            earliest, latest = flow.date_bounds(question, given, today)
            if earliest and chosen < earliest:
                chosen = earliest
            if latest and chosen > latest:
                chosen = latest
            return chosen.isoformat()
        case "passport":
            return {**SPECIMEN, "expiry_date": flow.add_months(today, 60).isoformat()}
        case "country":
            return "UTO"
        case "phone":
            return "+96171123456"
        case "address":
            return ADDRESS
    return "Example"


def walk_problems(questionnaire: flow.Questionnaire, today: date) -> list[str]:
    """Answer whatever the engine asks next with ordinary answers, as a traveler would, until it stops asking."""
    given: flow.Answers = {}
    while (question := flow.next_question(questionnaire, given)) is not None:
        try:
            given[question.id] = flow.check_answer(question, _ordinary_answer(question, given, today), given, today)
        except flow.InvalidAnswer as error:
            return [f"Question {question.id!r} refuses every ordinary answer ({error}), so travelers can't finish."]
    problems = []
    stopped = flow.stop_message(questionnaire, given)
    if stopped:
        problems.append(f"Ordinary answers stop the questionnaire before the end: {stopped}")
    if not any(q.type == "passport" and q.prefill == "passport" for q in questionnaire.questions()):
        problems.append('A passport question with "prefill": "passport" is needed: the completeness check and '
                        "the document cross-checks compare everything with it.")
    if not flow.required_documents(questionnaire, given):
        problems.append("List at least one supporting document that ordinary answers ask for.")
    return problems


def linked_problems(questionnaire: flow.Questionnaire) -> list[str]:
    """The official form guide and the embassy PDF map still find every question they use."""
    problems = []
    guide = portal_guide.read_guide(questionnaire.id)
    if guide is not None:
        try:
            portal_guide.check_guide(guide, questionnaire)
        except ValueError as error:
            problems.append(f"The official form guide (portals/{questionnaire.id}.json) needs it: {error}")
    form = form_pdf.read_form(questionnaire.id)
    if form is not None:
        try:
            form_pdf.check_form(*form, questionnaire)
        except ValueError as error:
            problems.append(f"The embassy PDF map (forms/{questionnaire.id}.json) needs it: {error}")
    return problems


def check(questionnaire_id: str, questionnaire: Any, guide: Any, *, version: int, today: date) -> Checked:
    """Check a destination's questionnaire and visa guide facts as the packaged files are checked.

    `version` replaces the questionnaire's own: the server numbers versions, not the author.
    """
    packaged = flow.packaged_questionnaire(questionnaire_id)
    problems: list[Problem] = []
    parsed = None
    if not isinstance(questionnaire, dict):
        problems.append(_problem("questionnaire", [], None, "The questionnaire must be a JSON object."))
    elif len(json.dumps(questionnaire)) > MAX_BYTES:
        problems.append(_problem("questionnaire", [], None, f"The questionnaire is larger than {MAX_BYTES} bytes."))
    else:
        data = {**questionnaire, "version": version}
        try:
            parsed = flow.Questionnaire.model_validate_json(json.dumps(data))
        except ValidationError as error:
            problems += _validation_problems("questionnaire", error, data)
    if parsed is not None:
        changed = [key for key in FIXED if getattr(parsed, key) != getattr(packaged, key)]
        problems += [_problem("questionnaire", [key], questionnaire,
                              f"Must stay {getattr(packaged, key)!r}: applications and pages find the visa by it.")
                     for key in changed]
        problems += [_problem("questionnaire", [], questionnaire, message)
                     for message in walk_problems(parsed, today) + linked_problems(parsed)]

    facts, checked_on = None, None
    if not isinstance(guide, dict):
        problems.append(_problem("guide", [], None, "The visa guide facts must be a JSON object."))
    else:
        entry = {key: value for key, value in guide.items() if key != "checked_on"}
        try:
            file = visa_guides.GuidesFile.model_validate(
                {"checked_on": guide.get("checked_on"), "guides": [{**entry, "questionnaire_id": questionnaire_id}]})
        except ValidationError as error:
            problems += _validation_problems("guide", error, guide, prefix=("guides", 0))
        else:
            facts, checked_on = file.guides[0], file.checked_on
            try:
                checked = date.fromisoformat(checked_on)
            except ValueError:
                problems.append(_problem("guide", ["checked_on"], guide, "Use a real date (YYYY-MM-DD)."))
            else:
                if checked > today:
                    problems.append(_problem("guide", ["checked_on"], guide, "The figures can't be checked in the future."))
    if problems:
        return Checked(problems=problems)
    return Checked(questionnaire=parsed, guide=facts, checked_on=checked_on)


def _list_key(old: Any, new: Any) -> str | None:
    if not (isinstance(old, list) and isinstance(new, list) and (old or new)):
        return None
    for key in ("id", "value"):
        names = [[item.get(key) if isinstance(item, dict) else None for item in items] for items in (old, new)]
        if all(isinstance(name, str) for group in names for name in group) and all(
                len(set(group)) == len(group) for group in names):
            return key
    return None


def changes(part: Literal["questionnaire", "guide"], old: Any, new: Any, path: str = "") -> list[Change]:
    """What changed from `old` to `new`, by question, step, document and option id where the lists have them."""
    if isinstance(old, dict) and isinstance(new, dict):
        found = []
        for key in [*old, *(key for key in new if key not in old)]:
            where = f"{path}.{key}" if path else key
            if key not in new:
                found.append(Change(part=part, path=where, kind="removed", before=old[key]))
            elif key not in old:
                found.append(Change(part=part, path=where, kind="added", after=new[key]))
            else:
                found += changes(part, old[key], new[key], where)
        return found
    key = _list_key(old, new)
    if key:
        before, after = {item[key]: item for item in old}, {item[key]: item for item in new}
        found = []
        for name, item in before.items():
            if name in after:
                found += changes(part, item, after[name], f"{path}[{name}]")
            else:
                found.append(Change(part=part, path=f"{path}[{name}]", kind="removed", before=item))
        found += [Change(part=part, path=f"{path}[{name}]", kind="added", after=item)
                  for name, item in after.items() if name not in before]
        if [name for name in before if name in after] != [name for name in after if name in before]:
            found.append(Change(part=part, path=path, kind="moved", before=list(before), after=list(after)))
        return found
    return [] if old == new else [Change(part=part, path=path, kind="changed", before=old, after=new)]
