from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
import pytest

from app.features import FLAGS, FeatureFlags, create_features_router, require_feature
from app.main import create_app


def clear_flag_overrides(monkeypatch):
    for name in FLAGS:
        monkeypatch.delenv(f"FEATURE_{name.upper()}", raising=False)


def test_every_flag_is_on_by_default_in_development(monkeypatch):
    clear_flag_overrides(monkeypatch)
    monkeypatch.setenv("APP_ENV", "development")
    assert FeatureFlags.from_environment().as_dict() == {name: True for name in FLAGS}


def test_every_flag_is_off_by_default_on_the_live_site(monkeypatch):
    clear_flag_overrides(monkeypatch)
    monkeypatch.setenv("APP_ENV", "production")
    assert FeatureFlags.from_environment().as_dict() == {name: False for name in FLAGS}


def test_the_environment_can_turn_one_flag_on_in_production_and_off_in_development(monkeypatch):
    clear_flag_overrides(monkeypatch)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("FEATURE_SUPPORTING_DOCUMENTS", "TRUE")
    live = FeatureFlags.from_environment()
    assert live.is_on("supporting_documents") and not live.is_on("agent_form_filling")

    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("FEATURE_AGENT_FORM_FILLING", "false")
    local = FeatureFlags.from_environment()
    assert local.is_on("supporting_documents") and not local.is_on("agent_form_filling")


@pytest.mark.parametrize("value", ["yes", "1", "on"])
def test_a_value_other_than_true_or_false_stops_startup(monkeypatch, value):
    monkeypatch.setenv("FEATURE_AGENT_FORM_FILLING", value)
    with pytest.raises(RuntimeError, match="FEATURE_AGENT_FORM_FILLING must be true or false"):
        FeatureFlags.from_environment()


def test_a_misspelt_flag_fails_when_the_route_is_built_not_on_a_request():
    with pytest.raises(KeyError):
        require_feature("suporting_documents")
    with pytest.raises(KeyError):
        FeatureFlags(frozenset()).is_on("suporting_documents")


def guarded_app(enabled: set[str]) -> FastAPI:
    app = FastAPI()
    app.state.feature_flags = FeatureFlags(frozenset(enabled))
    router = APIRouter(prefix="/documents",
                       dependencies=[Depends(require_feature("supporting_documents"))])

    @router.get("")
    def documents():
        return {"documents": []}

    app.include_router(router)
    app.include_router(create_features_router())
    return app


def test_a_flagged_route_answers_404_while_off_and_works_once_on():
    with TestClient(guarded_app(set())) as client:
        hidden = client.get("/documents")
        assert hidden.status_code == 404
        assert hidden.json() == {"detail": "Not Found"}
    with TestClient(guarded_app({"supporting_documents"})) as client:
        assert client.get("/documents").json() == {"documents": []}


def test_the_app_publishes_its_flags_without_sign_in_and_uncached(tmp_path, monkeypatch):
    clear_flag_overrides(monkeypatch)
    monkeypatch.setenv("FEATURE_AGENT_FORM_FILLING", "false")
    app = create_app(f"sqlite:///{(tmp_path / 'app.db').as_posix()}")
    with TestClient(app) as client:
        response = client.get("/features")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {"agent_form_filling": False, "supporting_documents": True}
