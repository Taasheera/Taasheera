"""Load operator-maintained form descriptions from versioned JSON."""
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import TypeAdapter

DIRECTORY = Path(__file__).resolve().parent
REPLICA_DIR = DIRECTORY / "replicas"
Kind = Literal["text", "select", "radio"]


@dataclass(frozen=True)
class FormField:
    key: str
    label: str
    selector: str
    kind: Kind = "text"
    required: bool = True


@dataclass(frozen=True)
class FormPage:
    title: str
    fields: tuple[FormField, ...]
    next: str | None = "button[data-next]"
    ready: str | None = None


@dataclass(frozen=True)
class FormSite:
    id: str
    name: str
    questionnaires: frozenset[str]
    start_url: str
    pages: tuple[FormPage, ...]
    submit: str
    page_scope: str = "body"
    date_format: str = "%d/%m/%Y"
    value_profile: str = "schengen-harmonised"
    url_override: str | None = None


def _load(path: Path) -> FormSite:
    data = json.loads(path.read_text(encoding="utf-8"))
    configured = os.environ.get(data.get("url_override") or "", "").strip()
    url = configured or data["start_url"]
    parsed = urlsplit(url)
    if parsed.scheme == "https" and parsed.hostname and not (parsed.username or parsed.password):
        data["start_url"] = url
    elif not configured and Path(url).name == url and (REPLICA_DIR / url).is_file():
        data["start_url"] = (REPLICA_DIR / url).as_uri()
    else:
        raise ValueError(f"{path.name}: expected an HTTPS portal or a bundled replica")
    site = TypeAdapter(FormSite).validate_python(data)
    if not site.pages or any(page.next is None for page in site.pages[:-1]) or site.pages[-1].next is not None:
        raise ValueError(f"{path.name}: the last page must mark the submission boundary")
    return site


SITES = {site.id: site for site in map(_load, sorted((DIRECTORY / "site_configs").glob("*.json")))}
SCHENGEN_HARMONISED = SITES["schengen-harmonised"]


def site_for(questionnaire_id: str) -> FormSite | None:
    candidates = [site for site in SITES.values() if questionnaire_id in site.questionnaires]
    # An HTTPS portal configured for this flow replaces its local replica.
    return next((site for site in candidates if site.start_url.startswith("https://")), candidates[0] if candidates else None)
