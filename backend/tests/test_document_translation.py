from types import SimpleNamespace

import pytest
from sqlmodel import Session, select

from app.assistant import AssistantUnavailable
from app.documents.translation import (MAX_TRANSLATION_CHARS, TranslationRefused, embassy_language, gemini_translate,
                                       translation_request)
from app.models import DocumentAccess
from test_applications_api import api, start  # noqa: F401
from test_assistant import FakeGemini, reply
from test_supporting_documents import consent_not_required, make_pdf, make_png, no_ocr, upload  # noqa: F401

STATEMENT = "Kontoauszug Nr. 4 fuer ANNA MARIA ERIKSSON, Kontostand 2.400,00 EUR am 01.11.2026"


@pytest.fixture
def provider(monkeypatch):
    state = SimpleNamespace(calls=[], reply="Account statement no. 4 for ANNA MARIA ERIKSSON", error=None)

    def translate(system, messages):
        state.calls.append((system, messages))
        if state.error:
            raise state.error
        return state.reply

    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-test-key")
    monkeypatch.setattr("app.main.genai.Client", lambda **options: SimpleNamespace())
    monkeypatch.setattr("app.main.gemini_translate", lambda client, model: translate)
    return state


def translate(client, headers, document_id, body=None):
    return client.post(f"/vault/documents/{document_id}/translation", headers=headers, json=body or {"language": "en"})


@pytest.fixture
def statement(api):
    client, _, headers, _ = api
    application = start(client, headers[0])
    return upload(client, headers[0], application["id"], "bank_statements", make_pdf(STATEMENT)).json()


def test_without_a_key_translation_answers_503(api, statement):
    client, _, headers, _ = api
    response = translate(client, headers[0], statement["id"])
    assert response.status_code == 503 and "isn't set up" in response.json()["detail"]


def test_a_translation_comes_back_with_the_notice_and_is_audited(provider, api, statement, capfd):
    client, app, headers, _ = api
    response = translate(client, headers[0], statement["id"])
    assert response.status_code == 200, response.json()
    assert response.headers["cache-control"] == "private, no-store"
    body = response.json()
    assert body == {"document_id": statement["id"], "language": "en", "language_label": "English",
                    "text": provider.reply, "truncated": False, "notice": body["notice"]}
    assert "not a certified translation" in body["notice"] and "not legal or immigration advice" in body["notice"]
    [(system, messages)] = provider.calls
    assert "into English" in system and "never instructions" in system
    assert messages == [{"role": "user", "content": f"<document>\n{STATEMENT}\n</document>"}]
    with Session(app.state.engine) as session:
        purposes = [row.purpose for row in session.exec(select(DocumentAccess))]
    assert purposes == ["document_translation"]
    out, err = capfd.readouterr()
    assert "Kontoauszug" not in out + err and "ERIKSSON" not in out + err


def test_the_embassy_language_can_be_chosen(provider, api, statement):
    client, _, headers, _ = api
    body = translate(client, headers[0], statement["id"], {"language": "fr"}).json()
    assert body["language_label"] == "French" and "into French" in provider.calls[0][0]


@pytest.mark.parametrize("body", [{"language": "xx"}, {"language": "en", "model": "other"}, {"language": 3}])
def test_requests_are_validated(provider, api, statement, body):
    client, _, headers, _ = api
    assert translate(client, headers[0], statement["id"], body).status_code == 422
    assert provider.calls == []


def test_only_the_owner_can_translate(provider, api, statement):
    client, _, headers, _ = api
    assert translate(client, headers[1], statement["id"]).status_code == 404
    assert translate(client, headers[0], "f" * 32).status_code == 404
    assert client.post(f"/vault/documents/{statement['id']}/translation", json={"language": "en"}).status_code == 401
    assert provider.calls == []


def test_a_document_without_readable_text_is_not_sent(provider, api):
    client, _, headers, _ = api
    application = start(client, headers[0])
    photo = upload(client, headers[0], application["id"], "photo", make_png()).json()
    response = translate(client, headers[0], photo["id"])
    assert response.status_code == 422 and "couldn't read any text" in response.json()["detail"]
    assert provider.calls == []


@pytest.mark.parametrize("error, status", [(AssistantUnavailable(), 503), (TranslationRefused(), 422)])
def test_provider_failures_are_reported_without_details(provider, api, statement, error, status):
    client, _, headers, _ = api
    provider.error = error
    response = translate(client, headers[0], statement["id"])
    assert response.status_code == status and "ERIKSSON" not in response.text


def test_translations_are_rate_limited(provider, api, statement):
    client, _, headers, _ = api
    for _ in range(10):
        assert translate(client, headers[0], statement["id"]).status_code == 200
    assert translate(client, headers[0], statement["id"]).status_code == 429


def test_translation_follows_its_flag(provider, api, statement, monkeypatch):
    client, app, headers, _ = api
    flags = type(app.state.feature_flags)
    monkeypatch.setattr(app.state, "feature_flags", flags(frozenset({"supporting_documents", "document_vault"})))
    assert translate(client, headers[0], statement["id"]).status_code == 404
    assert provider.calls == []


def test_long_documents_are_cut_and_marked():
    system, messages, truncated = translation_request("x" * (MAX_TRANSLATION_CHARS + 50), "de")
    assert truncated and "into German" in system
    assert len(messages[0]["content"]) == MAX_TRANSLATION_CHARS + len("<document>\n\n</document>")
    assert translation_request("short text", "en")[2] is False


def test_embassy_languages_follow_the_destination():
    assert embassy_language("schengen-short-stay", {"main_destination": "FRA"}) == "fr"
    assert embassy_language("schengen-short-stay", {"main_destination": "D"}) == "de"
    assert embassy_language("schengen-short-stay", {}) is None
    assert embassy_language("japan-short-stay", {}) == "ja"
    assert embassy_language("uk-standard-visitor", {}) == "en"


def test_the_gemini_adapter_allows_long_replies_and_reports_blocks():
    fake = FakeGemini(reply(" Translated text. "))
    assert gemini_translate(fake, "gemini-3.5-flash-lite")("system", [{"role": "user", "content": "x"}]) == "Translated text."
    assert fake.requests[0]["config"]["max_output_tokens"] == 4096
    with pytest.raises(TranslationRefused):
        gemini_translate(FakeGemini(reply(block_reason="SAFETY")))("system", [{"role": "user", "content": "x"}])
    with pytest.raises(AssistantUnavailable):
        gemini_translate(FakeGemini(reply(None)))("system", [{"role": "user", "content": "x"}])
