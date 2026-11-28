"""Cover letter, itinerary and letter templates written from the answers (CMPS271-57/58/59/60/61/62)."""

import io
from datetime import date, timedelta

from pypdf import PdfReader
from sqlmodel import Session, select

from app.applications import flow
from app.generated.templates import available_kinds, embassy_languages, facts, generate
from app.models import GeneratedDocument
from test_applications_api import ANSWERS, ARRIVAL, answer_all, api, start  # noqa: F401 (api is a fixture)

TODAY = date(2026, 11, 28)
SCHENGEN = flow.load_questionnaire("schengen-short-stay")


def schengen(**changes):
    answers = dict(ANSWERS, **changes)
    return facts(SCHENGEN, answers, "traveler@example.com"), answers


def url(application_id, path=""):
    return f"/applications/{application_id}/generated{path}"


def ready(client, headers):
    application_id = start(client, headers)["id"]
    answer_all(client, headers, application_id)
    return application_id


def test_the_cover_letter_says_only_what_the_answers_say():
    f, _ = schengen()
    letter = generate("cover_letter", "en", f, TODAY).body
    name = f"{ANSWERS['passport']['given_names'].title()} {ANSWERS['passport']['surname'].title()}".strip()
    assert name in letter and ANSWERS["passport"]["document_number"] in letter
    assert "visit France for tourism" in letter and "(10 nights)" in letter
    assert "I will stay with my host: Lina Example, 2 Rue Exemple, Paris." in letter
    assert "I am retired." in letter and "I will pay for the trip myself" in letter
    assert "return home in Beirut" in letter and "traveler@example.com" in letter
    # Nothing the traveler didn't say: no employer, no sponsor, no placeholder blanks.
    assert "[" not in letter and "employer" not in letter and "sponsor" not in letter


def test_an_employed_sponsored_traveler_gets_those_sentences():
    f, _ = schengen(occupation="employed", job_title="Accountant", employer="Example Ltd", funding="sponsor",
                    sponsor_details="My aunt, Rana Example", accommodation="hotel",
                    accommodation_details="Hotel Exemple, Lyon")
    letter = generate("cover_letter", "en", f, TODAY).body
    assert "I work as Accountant at Example Ltd" in letter
    assert "paid for by my sponsor: My aunt, Rana Example" in letter
    assert "I will stay at Hotel Exemple, Lyon." in letter


def test_the_embassy_language_is_offered_where_we_translate_it():
    assert embassy_languages(SCHENGEN, dict(ANSWERS, main_destination="FRA")) == ["en", "fr"]
    assert embassy_languages(SCHENGEN, dict(ANSWERS, main_destination="DEU")) == ["en", "de"]
    assert embassy_languages(SCHENGEN, dict(ANSWERS, main_destination="GRC")) == ["en"]
    assert embassy_languages(flow.load_questionnaire("canada-visitor"), {}) == ["en", "fr"]
    assert embassy_languages(flow.load_questionnaire("uk-standard-visitor"), {}) == ["en"]


def test_translated_documents_use_fixed_wording_and_keep_the_travelers_own_words():
    f, _ = schengen()
    french = generate("cover_letter", "fr", f, TODAY)
    assert french.title == "Lettre de motivation"
    assert "me rendre en France" in french.body and "Motif du voyage : tourisme" in french.body
    assert "Lina Example, 2 Rue Exemple, Paris" in french.body and "(10 nuits)" in french.body
    assert f"{ARRIVAL.day} " in french.body
    german = generate("itinerary", "de", schengen(main_destination="DEU")[0], TODAY)
    assert german.title == "Reiseplan" and "Tag 1" in german.body and "Ankunft nach Deutschland" in german.body


def test_the_itinerary_has_one_entry_per_day_and_spreads_the_cities():
    f, _ = schengen(itinerary_cities="Paris, Lyon and Nice")
    body = generate("itinerary", "en", f, TODAY).body
    days = [line for line in body.splitlines() if line.startswith("Day ")]
    assert len(days) == 11  # ten nights
    assert days[0].endswith("· Paris") and days[-1].endswith("· Nice")
    assert "Travel from Paris to Lyon." in body and "Travel from Lyon to Nice." in body
    assert body.count("Overnight:") == 10


def test_without_dates_the_itinerary_asks_for_them():
    f, _ = schengen(arrival_date=None, departure_date=None)
    assert "Add your arrival and departure dates" in generate("itinerary", "en", f, TODAY).body


def test_letter_templates_are_offered_when_the_destination_asks_for_them():
    _, answers = schengen()
    assert available_kinds(SCHENGEN, answers) == ["cover_letter", "itinerary", "invitation_letter"]
    _, answers = schengen(occupation="employed", funding="sponsor", accommodation="hotel")
    assert available_kinds(SCHENGEN, answers) == ["cover_letter", "itinerary", "sponsor_letter", "employment_letter"]


def test_letter_templates_prefill_the_traveler_and_leave_blanks_for_the_signer():
    f, _ = schengen(occupation="employed", job_title="Accountant", employer="Example Ltd")
    letter = generate("employment_letter", "en", f, TODAY).body
    assert "employed by Example Ltd" in letter and "as Accountant" in letter
    assert "[start date]" in letter and "[Signature and company stamp]" in letter
    resume = ARRIVAL + timedelta(days=11)
    assert f"resume work on {resume.day} " in letter
    invitation = generate("invitation_letter", "en", schengen()[0], TODAY).body
    assert "Lina Example, 2 Rue Exemple, Paris" in invitation and "[Host's full name]" in invitation


def test_the_api_lists_reads_edits_regenerates_and_prints(api):
    client, app, headers, _ = api
    application_id = ready(client, headers[0])
    overview = client.get(url(application_id), headers=headers[0])
    assert overview.status_code == 200 and overview.headers["cache-control"] == "private, no-store"
    kinds = {item["kind"]: [language["language"] for language in item["languages"]] for item in overview.json()["documents"]}
    assert kinds == {"cover_letter": ["en", "fr"], "itinerary": ["en", "fr"], "invitation_letter": ["en"]}
    assert "legal or immigration advice" in overview.json()["notice"]

    letter = client.get(url(application_id, "/cover_letter"), headers=headers[0]).json()
    assert letter["edited"] is False and "visit France" in letter["body"]

    edited = client.put(url(application_id, "/cover_letter"), headers=headers[0], json={"body": "My own words."})
    assert edited.status_code == 200 and edited.json()["edited"] is True and edited.json()["body"] == "My own words."
    assert client.get(url(application_id, "/cover_letter"), headers=headers[0]).json()["body"] == "My own words."
    # The French version is a separate document and still the generated one.
    assert client.get(url(application_id, "/cover_letter?language=fr"), headers=headers[0]).json()["edited"] is False

    pdf = client.get(url(application_id, "/cover_letter/pdf"), headers=headers[0])
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"
    assert "My own words." in PdfReader(io.BytesIO(pdf.content)).pages[0].extract_text() or len(pdf.content) > 1000

    regenerated = client.delete(url(application_id, "/cover_letter"), headers=headers[0]).json()
    assert regenerated["edited"] is False and "visit France" in regenerated["body"]
    with Session(app.state.engine) as session:
        assert session.exec(select(GeneratedDocument)).all() == []


def test_an_edit_is_marked_outdated_when_answers_change_later(api):
    client, _, headers, _ = api
    application_id = ready(client, headers[0])
    client.put(url(application_id, "/itinerary"), headers=headers[0], json={"body": "Day 1: Paris."})
    changed = client.put(f"/applications/{application_id}/answers/host_details", headers=headers[0],
                         json={"value": "Lina Example, 3 Rue Exemple, Paris"})
    assert changed.status_code == 200
    document = client.get(url(application_id, "/itinerary"), headers=headers[0]).json()
    assert document["edited"] is True and document["outdated"] is True


def test_documents_are_owner_only_and_unknown_kinds_or_languages_are_404(api):
    client, _, headers, _ = api
    application_id = ready(client, headers[0])
    assert client.get(url(application_id), headers=headers[1]).status_code == 404
    assert client.put(url(application_id, "/cover_letter"), headers=headers[1], json={"body": "x"}).status_code == 404
    assert client.get(url(application_id, "/employment_letter"), headers=headers[0]).status_code == 404
    assert client.get(url(application_id, "/invitation_letter?language=fr"), headers=headers[0]).status_code == 404
    assert client.get(url(application_id, "/cover_letter?language=de"), headers=headers[0]).status_code == 404
    assert client.put(url(application_id, "/cover_letter"), headers=headers[0],
                      json={"body": "x", "extra": 1}).status_code == 422


def test_archived_applications_print_but_do_not_edit(api):
    client, _, headers, _ = api
    application_id = ready(client, headers[0])
    assert client.post(f"/applications/{application_id}/archive", headers=headers[0]).status_code == 200
    assert client.get(url(application_id), headers=headers[0]).json()["read_only"] is True
    assert client.put(url(application_id, "/cover_letter"), headers=headers[0], json={"body": "x"}).status_code == 409
    assert client.get(url(application_id, "/cover_letter/pdf"), headers=headers[0]).status_code == 200


def test_the_packet_uses_the_generated_cover_letter_when_none_was_uploaded(api):
    client, _, headers, _ = api
    application_id = ready(client, headers[0])
    packet = client.get(f"/applications/{application_id}/documents/packet", headers=headers[0])
    assert packet.status_code == 200
    # Nothing is uploaded, so without generated documents the packet would be its cover page alone.
    # The cover letter and itinerary follow it (the itinerary isn't on Schengen's list, so only the letter).
    assert len(PdfReader(io.BytesIO(packet.content)).pages) == 2


def test_deleting_the_application_removes_its_edits(api):
    client, app, headers, _ = api
    application_id = ready(client, headers[0])
    client.put(url(application_id, "/cover_letter"), headers=headers[0], json={"body": "Mine."})
    assert client.delete(f"/applications/{application_id}", headers=headers[0]).status_code == 204
    with Session(app.state.engine) as session:
        assert session.exec(select(GeneratedDocument)).all() == []
