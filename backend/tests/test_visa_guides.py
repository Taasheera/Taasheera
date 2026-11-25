"""Public visa guides (CMPS271-19, 20, 21, 22, 24, 25)."""

import json
import re
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.applications import flow, visa_guides
from app.applications.policy_sources import SOURCES
from app.main import create_app

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(f"sqlite:///{(tmp_path / 'guides.db').as_posix()}")) as client:
        yield client


def test_every_supported_visa_has_a_public_guide(client):
    response = client.get("/visa-guides")  # no sign-in
    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=3600"
    guides = response.json()
    assert [guide["id"] for guide in guides] == [q.id for q in flow.available_questionnaires()]
    for guide in guides:
        assert client.get(f"/visa-guides/{guide['id']}").json() == guide


def test_an_unknown_guide_is_not_found(client):
    assert client.get("/visa-guides/mars-tourist").status_code == 404


def test_the_checklist_and_limits_come_from_the_application_itself(client):
    guides = {guide["id"]: guide for guide in client.get("/visa-guides").json()}
    for questionnaire in flow.available_questionnaires():
        guide = guides[questionnaire.id]
        assert [d["label"] for d in guide["documents"]] == [d.label for d in questionnaire.documents]
        assert guide["not_for"] == [q.stop_if.message for q in questionnaire.questions() if q.stop_if]
        assert guide["title"] == questionnaire.title

    uk = guides["uk-standard-visitor"]
    invitation = next(d for d in uk["documents"] if d["label"] == "Invitation from your host")
    assert invitation["only_if"] == 'Only if your answer to "Will you stay with family or friends?" is "Yes".'
    bank = next(d for d in uk["documents"] if d["label"] == "Bank statements")
    assert bank["only_if"] is None
    schengen = guides["schengen-short-stay"]
    assert any('"A hotel", "A rented flat or house" or "Somewhere else"' in (d["only_if"] or "")
               for d in schengen["documents"])


def test_fees_and_times_are_the_figures_checked_on_official_sites(client):
    guides = {guide["id"]: guide for guide in client.get("/visa-guides").json()}
    amounts = {key: [item["amount"] for item in guide["fees"]["items"]] for key, guide in guides.items()}
    assert amounts == {"canada-visitor": ["CAD 100", "CAD 85"], "schengen-short-stay": ["€90", "€45", "Free"],
                       "uk-standard-visitor": ["£135", "£506"], "us-b2-tourist": ["$185"],
                       "china-tourist-l": ["US$23", "US$45", "US$44.40"], "japan-short-stay": ["About ¥15,000", "About ¥30,000"],
                       "india-tourist": ["US$103", "US$25", "US$40"], "turkiye-evisa": ["Varies by passport"]}
    assert "15 calendar days" in guides["schengen-short-stay"]["time"]["text"]
    assert "3 weeks" in guides["uk-standard-visitor"]["time"]["text"]
    assert {guide["checked_on"] for guide in guides.values()} == {"2026-11-25"}


def test_every_link_is_an_official_https_source_and_the_visa_page_comes_first(client):
    for guide in client.get("/visa-guides").json():
        assert guide["sources"][0] == guide["official_page"]
        assert guide["official_page"]["url"] == SOURCES[guide["id"]][1]
        urls = [link["url"] for link in guide["sources"]]
        assert len(urls) == len(set(urls))
        for url in urls + [guide["need"]["checker"]["url"], guide["fees"]["source"]["url"]]:
            assert url.startswith("https://")


@pytest.mark.parametrize("url", ["http://www.gov.uk/check-uk-visa", "https://visa-help.example.com/uk",
                                 "https://gov.uk.example.com/", "https://evilgov.uk/"])
def test_a_link_to_an_unofficial_site_is_refused(url):
    with pytest.raises(ValidationError, match="allowed official site"):
        visa_guides.Link(label="Somewhere", url=url)


def test_lebanese_travellers_get_a_checked_answer_for_every_visa(client):
    for guide in client.get("/visa-guides").json():
        expected = "no_visa" if guide["id"] == "turkiye-evisa" else "visa"
        assert guide["need"]["checked_nationalities"] == {"LBN": expected}
        assert guide["need"]["checker"]["url"].startswith("https://")


def test_the_data_file_matches_the_questionnaires():
    data = json.loads(visa_guides.GUIDES_FILE.read_text(encoding="utf-8"))
    assert {guide["questionnaire_id"] for guide in data["guides"]} == {q.id for q in flow.available_questionnaires()}
    assert date.fromisoformat(data["checked_on"]) <= date.today()


def test_a_questionnaire_without_a_guide_stops_startup(monkeypatch, tmp_path):
    data = json.loads(visa_guides.GUIDES_FILE.read_text(encoding="utf-8"))
    data["guides"] = data["guides"][1:]
    broken = tmp_path / "visa_guides.json"
    broken.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(visa_guides, "GUIDES_FILE", broken)
    visa_guides.load_guides.cache_clear()
    try:
        with pytest.raises(ValueError, match="disagree"):
            visa_guides.load_guides()
    finally:
        visa_guides.load_guides.cache_clear()


def test_the_proxies_forward_the_guides_to_the_api():
    assert re.search(r"\|visa-guides\|", (ROOT / "deploy/aws/nginx.conf").read_text(encoding="utf-8"))
    assert "'visa-guides'" in (ROOT / "frontend/vite.config.ts").read_text(encoding="utf-8")


def test_with_the_flag_off_the_guides_are_hidden(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATURE_VISA_GUIDE", "false")
    with TestClient(create_app(f"sqlite:///{(tmp_path / 'off.db').as_posix()}")) as client:
        assert client.get("/visa-guides").status_code == 404
        assert client.get("/features").json()["visa_guide"] is False
