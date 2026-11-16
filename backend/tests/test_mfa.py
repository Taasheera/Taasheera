"""Authenticator enrollment, first-factor isolation, replay, recovery and ownership."""
import time
from types import SimpleNamespace

import bcrypt
import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlmodel import Session, select

from app.main import create_app
from app.models import AuthSession, GoogleIdentity, MfaBackupCode, MfaChallenge, MfaSettings, Traveler

PASSWORD = "Synthetic-password-123"
EMAIL = "mfa-owner@example.com"


@pytest.fixture
def api(tmp_path, monkeypatch):
    clock = [int(time.time())]
    monkeypatch.setattr("app.mfa.now", lambda: clock[0])
    app = create_app(f"sqlite:///{tmp_path / 'mfa.db'}")
    with TestClient(app) as client:
        with Session(app.state.engine) as session:
            owner = Traveler(name="MFA owner", email=EMAIL,
                password_hash=bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt(rounds=4)).decode())
            session.add(owner); session.commit()
        result = client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})
        yield SimpleNamespace(client=client, app=app, headers={"Authorization": "Bearer " + result.json()["access_token"]},
                              clock=clock, monkeypatch=monkeypatch)


def enroll(api):
    setup = api.client.post("/auth/mfa/setup", headers=api.headers,
        json={"expected_traveler_id": 1, "password": PASSWORD})
    assert setup.status_code == 200, setup.text
    secret = setup.json()["secret"]
    assert setup.json()["qr_data_url"].startswith("data:image/png;base64,")
    response = api.client.post("/auth/mfa/enable", headers=api.headers,
        json={"expected_traveler_id": 1, "code": pyotp.TOTP(secret).at(api.clock[0])})
    assert response.status_code == 200, response.text
    return secret, response.json()["backup_codes"]


def login(api):
    return api.client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})


def verify(api, challenge, code):
    return api.client.post("/auth/mfa/verify", json={"challenge_token": challenge, "code": code})


def test_password_alone_issues_no_session_and_totp_completes_rotating_session(api):
    secret, backups = enroll(api)
    assert len(backups) == len(set(backups)) == 10
    api.client.post("/auth/logout")
    pending = login(api)
    assert pending.status_code == 202 and "access_token" not in pending.json()
    assert "set-cookie" not in pending.headers
    token = pending.json()["challenge_token"]
    assert api.client.get("/auth/me", headers={"Authorization": "Bearer " + token}).status_code == 401
    with Session(api.app.state.engine) as session:
        assert not session.exec(select(AuthSession).where(AuthSession.revoked.is_(False))).all()
        settings = session.get(MfaSettings, 1)
        assert secret not in settings.secret_ciphertext
        assert all(code not in str(session.exec(select(MfaBackupCode)).all()) for code in backups)
    # The enrollment code is one-use too. A fresh counter is needed for sign-in.
    assert verify(api, token, pyotp.TOTP(secret).at(api.clock[0])).status_code == 401
    api.clock[0] += 30
    result = verify(api, token, pyotp.TOTP(secret).at(api.clock[0]))
    assert result.status_code == 200 and "access_token" in result.json()
    assert "HttpOnly" in result.headers["set-cookie"]
    headers = {"Authorization": "Bearer " + result.json()["access_token"]}
    assert api.client.get("/auth/me", headers=headers).json()["id"] == 1
    assert verify(api, token, backups[0]).status_code == 401
    assert api.client.post("/auth/refresh").status_code == 200


def test_backup_code_is_one_use_and_disable_requires_a_current_second_factor(api):
    _, backups = enroll(api)
    token = login(api).json()["challenge_token"]
    result = verify(api, token, backups[0])
    assert result.status_code == 200
    headers = {"Authorization": "Bearer " + result.json()["access_token"]}
    next_token = login(api).json()["challenge_token"]
    assert verify(api, next_token, backups[0]).status_code == 401
    assert api.client.post("/auth/mfa/disable", headers=headers,
        json={"expected_traveler_id": 1, "code": "000000"}).status_code == 403
    assert api.client.post("/auth/mfa/disable", headers=headers,
        json={"expected_traveler_id": 1, "code": backups[1]}).status_code == 200
    assert login(api).status_code == 200
    assert verify(api, next_token, backups[2]).status_code == 401
    with Session(api.app.state.engine) as session:
        assert session.exec(select(MfaBackupCode)).all() == []


def test_enrollment_reauth_owner_origin_and_expiration(api):
    assert api.client.post("/auth/mfa/setup", json={"expected_traveler_id": 1, "password": PASSWORD}).status_code == 401
    assert api.client.post("/auth/mfa/setup", headers=api.headers,
        json={"expected_traveler_id": 2, "password": PASSWORD}).status_code == 409
    assert api.client.post("/auth/mfa/setup", headers={**api.headers, "Origin": "https://evil.example"},
        json={"expected_traveler_id": 1, "password": PASSWORD}).status_code == 403
    assert api.client.post("/auth/mfa/setup", headers=api.headers,
        json={"expected_traveler_id": 1, "password": "wrong-password"}).status_code == 403
    setup = api.client.post("/auth/mfa/setup", headers=api.headers,
        json={"expected_traveler_id": 1, "password": PASSWORD}).json()
    api.clock[0] += 601
    assert api.client.post("/auth/mfa/enable", headers=api.headers,
        json={"expected_traveler_id": 1, "code": pyotp.TOTP(setup["secret"]).at(api.clock[0])}).status_code == 409


@pytest.mark.parametrize("change", ["expired", "suspended", "password_changed", "cancelled", "superseded"])
def test_stale_first_factor_cannot_complete_sign_in(api, change):
    _, backups = enroll(api)
    challenge = login(api).json()["challenge_token"]
    if change == "expired": api.clock[0] += 301
    elif change == "cancelled":
        assert api.client.post("/auth/mfa/cancel", json={"challenge_token": challenge}).status_code == 204
    elif change == "superseded": assert login(api).status_code == 202
    else:
        with Session(api.app.state.engine) as session:
            owner = session.get(Traveler, 1)
            if change == "suspended": owner.status = "suspended"
            else: owner.password_hash = bcrypt.hashpw(b"Changed-password", bcrypt.gensalt(rounds=4)).decode()
            session.add(owner); session.commit()
    assert verify(api, challenge, backups[0]).status_code == 401


def test_failed_attempts_are_bounded_and_a_valid_code_cannot_revive_a_burned_challenge(api):
    _, backups = enroll(api)
    challenge = login(api).json()["challenge_token"]
    for _ in range(5): assert verify(api, challenge, "not-a-valid-code").status_code == 401
    assert verify(api, challenge, backups[0]).status_code == 401


def test_google_sign_in_also_requires_second_factor(api):
    _, backups = enroll(api)
    api.app.state.auth_settings = type(api.app.state.auth_settings)(**{
        **vars(api.app.state.auth_settings), "google_client_id": "synthetic.apps.googleusercontent.com"})
    api.monkeypatch.setattr("app.google_auth.verify_google_token", lambda *args: {
        "sub": "synthetic-subject", "email": EMAIL, "name": "Ignored name"})
    with Session(api.app.state.engine) as session:
        session.add(GoogleIdentity(subject="synthetic-subject", traveler_id=1)); session.commit()
    result = api.client.post("/auth/google", json={"id_token": "synthetic"})
    assert result.status_code == 202 and "access_token" not in result.json()
    assert verify(api, result.json()["challenge_token"], backups[0]).status_code == 200


def test_public_google_link_login_does_not_change_identity_before_mfa(api):
    enroll(api)
    api.app.state.auth_settings = type(api.app.state.auth_settings)(**{
        **vars(api.app.state.auth_settings), "google_client_id": "synthetic.apps.googleusercontent.com"})
    api.monkeypatch.setattr("app.google_auth.verify_google_token", lambda *args: {
        "sub": "unlinked-subject", "email": EMAIL, "name": "Ignored name"})
    response = api.client.post("/auth/google/link-login", json={"id_token": "synthetic", "password": PASSWORD})
    assert response.status_code == 409
    with Session(api.app.state.engine) as session:
        assert session.exec(select(GoogleIdentity)).all() == []


def test_backup_regeneration_invalidates_old_codes_and_pending_challenges(api):
    _, backups = enroll(api)
    old_challenge = login(api).json()['challenge_token']
    result = api.client.post('/auth/mfa/backup-codes', headers=api.headers,
        json={'expected_traveler_id': 1, 'code': backups[0]})
    assert result.status_code == 200, result.text
    replacements = result.json()['backup_codes']
    assert set(backups).isdisjoint(replacements)
    assert verify(api, old_challenge, replacements[0]).status_code == 401
    challenge = login(api).json()['challenge_token']
    assert verify(api, challenge, backups[1]).status_code == 401
    assert verify(api, challenge, replacements[0]).status_code == 200


def test_flag_off_still_enforces_existing_mfa_and_allows_safe_disable(api):
    from app.features import FeatureFlags
    _, backups = enroll(api)
    api.app.state.feature_flags = FeatureFlags(frozenset())
    assert api.client.post('/auth/mfa/setup', headers=api.headers,
        json={'expected_traveler_id': 1, 'password': PASSWORD}).status_code == 404
    result = login(api)
    assert result.status_code == 202
    completed = verify(api, result.json()['challenge_token'], backups[0])
    assert completed.status_code == 200
    headers = {'Authorization': 'Bearer ' + completed.json()['access_token']}
    assert api.client.post('/auth/mfa/disable', headers=headers,
        json={'expected_traveler_id': 1, 'code': backups[1]}).status_code == 200


def test_google_only_setup_requires_bound_google_subject_and_enrollment_revokes_other_devices(api):
    api.client.cookies.clear()
    other = login(api).json()['access_token']
    with Session(api.app.state.engine) as session:
        owner = session.get(Traveler, 1)
        owner.auth_type = 'google'; owner.password_hash = ''; owner.sso_email = EMAIL
        session.add(owner); session.add(GoogleIdentity(subject='bound-subject', traveler_id=1)); session.commit()
    api.app.state.auth_settings = type(api.app.state.auth_settings)(**{
        **vars(api.app.state.auth_settings), 'google_client_id': 'synthetic.apps.googleusercontent.com'})
    api.monkeypatch.setattr('app.google_auth.verify_google_token', lambda *args: {'sub': 'wrong-subject', 'email': EMAIL})
    body = {'expected_traveler_id': 1, 'google_id_token': 'synthetic'}
    assert api.client.post('/auth/mfa/setup', headers=api.headers, json=body).status_code == 403
    api.monkeypatch.setattr('app.google_auth.verify_google_token', lambda *args: {'sub': 'bound-subject', 'email': EMAIL})
    setup = api.client.post('/auth/mfa/setup', headers=api.headers, json=body)
    assert setup.status_code == 200
    assert api.client.post('/auth/mfa/enable', headers=api.headers, json={'expected_traveler_id': 1,
        'code': pyotp.TOTP(setup.json()['secret']).at(api.clock[0])}).status_code == 200
    assert api.client.get('/auth/me', headers={'Authorization': 'Bearer ' + other}).status_code == 401
