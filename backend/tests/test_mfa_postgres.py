"""Real PostgreSQL row locks must prevent duplicate backup/challenge consumption."""
from concurrent.futures import ThreadPoolExecutor
import os
from threading import Barrier
from uuid import uuid4

import pyotp
import pytest
from fastapi.testclient import TestClient

import migrate
from app.main import create_app


@pytest.mark.skipif(not os.environ.get('TEST_POSTGRES_URL'), reason='needs disposable PostgreSQL')
def test_concurrent_second_factor_is_consumed_exactly_once(monkeypatch):
    monkeypatch.setenv('DATABASE_URL', os.environ['TEST_POSTGRES_URL'])
    migrate.upgrade()
    app = create_app()
    email = f'mfa-pg-{uuid4().hex}@example.com'
    password = 'Synthetic-test-password-123'
    with TestClient(app) as first, TestClient(app) as second:
        created = first.post('/auth/register', json={'name': 'Synthetic', 'email': email, 'password': password})
        owner = created.json()['id']
        access = first.post('/auth/login', json={'email': email, 'password': password}).json()['access_token']
        headers = {'Authorization': 'Bearer ' + access}
        setup = first.post('/auth/mfa/setup', headers=headers, json={'expected_traveler_id': owner, 'password': password})
        secret = setup.json()['secret']
        enabled = first.post('/auth/mfa/enable', headers=headers, json={'expected_traveler_id': owner, 'code': pyotp.TOTP(secret).now()})
        backup = enabled.json()['backup_codes'][0]
        first.cookies.clear()
        pending = first.post('/auth/login', json={'email': email, 'password': password}).json()['challenge_token']
        barrier = Barrier(2)
        def submit(client):
            barrier.wait()
            return client.post('/auth/mfa/verify', json={'challenge_token': pending, 'code': backup}).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = sorted(pool.map(submit, (first, second)))
        assert results == [200, 401]
