import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.main import create_app
from app.models import ActivityEvent, GoogleIdentity, MfaSettings, PasswordResetChallenge, Traveler
from app.password_reset import delivery_reason


@pytest.fixture
def recovery(tmp_path, monkeypatch):
    monkeypatch.setenv('SMTP_HOST', '127.0.0.1')
    monkeypatch.setenv('SMTP_FROM', 'qa@example.com')
    monkeypatch.setenv('RESET_FRONTEND_URL', 'http://127.0.0.1:5173/reset-password')
    class Sender:
        messages = None
        def __init__(self): self.messages = []
        def send_reset(self, email, code): self.messages.append((email, code))
    sender = Sender()
    app = create_app(f"sqlite:///{tmp_path / 'recovery.db'}", reset_sender=sender)
    with TestClient(app) as client:
        with Session(app.state.engine) as session:
            session.add(Traveler(name='Google specimen', email='google@example.com', auth_type='google',
                                 sso_email='google@example.com', password_hash=''))
            session.commit()
            session.add(GoogleIdentity(subject='synthetic-sub', traveler_id=1))
            session.add(MfaSettings(traveler_id=1, enabled=True, secret_ciphertext='opaque-existing-secret'))
            session.commit()
        yield client, app, sender


def test_google_only_recovery_adds_password_preserves_google_and_mfa(recovery):
    client, app, sender = recovery
    known = client.post('/auth/forgot-password', json={'email': 'google@example.com'})
    unknown = client.post('/auth/forgot-password', json={'email': 'absent@example.com'})
    assert known.status_code == unknown.status_code == 202 and known.json() == unknown.json()
    assert len(sender.messages) == 1
    token = client.post('/auth/verify-reset-code', json={'email': 'google@example.com', 'code': sender.messages[0][1]})
    assert token.status_code == 200, token.text
    assert client.post('/auth/reset-password', json={'token': token.json()['reset_token'],
                       'password': 'New-synthetic-password-123'}).status_code == 204
    assert client.post('/auth/reset-password', json={'token': token.json()['reset_token'],
                       'password': 'New-synthetic-password-456'}).status_code == 400
    with Session(app.state.engine) as session:
        owner = session.get(Traveler, 1)
        assert owner.password_enabled and owner.sso_email == 'google@example.com'
        assert session.get(GoogleIdentity, 'synthetic-sub').traveler_id == owner.id
        assert session.get(MfaSettings, owner.id).enabled
        event = session.exec(select(ActivityEvent).where(ActivityEvent.event_type == 'password_reset')).one()
        assert event.actor_id == owner.id and event.resource_type == 'security'
    signin = client.post('/auth/login', json={'email': 'google@example.com', 'password': 'New-synthetic-password-123'})
    assert signin.status_code == 202 and 'access_token' not in signin.json()


def test_production_flag_preserves_previous_google_only_policy(recovery):
    from app.features import FeatureFlags
    client, app, sender = recovery
    app.state.feature_flags = FeatureFlags(frozenset())
    assert client.post('/auth/forgot-password', json={'email': 'google@example.com'}).status_code == 202
    assert sender.messages == []


def test_unverified_ses_rejection_invalidates_code_and_logs_only_safe_reason(recovery, caplog):
    client, app, sender = recovery
    def rejected(email, code):
        sender.messages.append((email, code))
        raise ClientError({'Error': {'Code': 'MessageRejected',
            'Message': f'Email address is not verified: {email}; secret code {code}'}}, 'SendEmail')
    sender.send_reset = rejected
    result = client.post('/auth/forgot-password', json={'email': 'google@example.com'})
    assert result.status_code == 202
    assert any(getattr(record, 'activity', {}).get('reason') == 'ses_unverified_identity' for record in caplog.records)
    assert 'google@example.com' not in caplog.text and sender.messages[0][1] not in caplog.text
    with Session(app.state.engine) as session:
        assert session.exec(select(PasswordResetChallenge)).all() == []
    assert client.post('/auth/verify-reset-code', json={'email': 'google@example.com',
                       'code': sender.messages[0][1]}).status_code == 400


@pytest.mark.parametrize(('code', 'reason'), [('AccessDeniedException', 'ses_access_denied'),
    ('Throttling', 'ses_rate_limited'), ('AccountSuspendedException', 'ses_rejected'),
    ('UnrecognizedClientException', 'delivery_unavailable')])
def test_provider_reasons_are_a_fixed_allowlist(code, reason):
    assert delivery_reason(ClientError({'Error': {'Code': code, 'Message': 'sensitive'}}, 'SendEmail')) == reason
