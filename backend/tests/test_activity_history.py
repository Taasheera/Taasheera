from datetime import datetime, timezone

import bcrypt
from fastapi.testclient import TestClient
import pytest
from sqlmodel import Session, select

from app.activity_history import boundary
from app.document_audit import record_document_access
from app.main import create_app
from app.models import ActivityEvent, Traveler


@pytest.fixture
def api(tmp_path):
    app = create_app(f"sqlite:///{tmp_path / 'history.db'}")
    with TestClient(app) as client:
        with Session(app.state.engine) as session:
            for role in ('admin', 'traveler', 'traveler'):
                count = len(session.exec(select(Traveler)).all()) + 1
                session.add(Traveler(name='Synthetic', email=f'user{count}@example.com', role=role,
                    password_hash=bcrypt.hashpw(b'History-password-123', bcrypt.gensalt(rounds=4)).decode()))
                session.commit()
        headers = []
        for number in (1, 2, 3):
            client.cookies.clear()
            token = client.post('/auth/login', json={'email': f'user{number}@example.com',
                                                    'password': 'History-password-123'}).json()['access_token']
            headers.append({'Authorization': 'Bearer ' + token})
        with Session(app.state.engine) as session:
            for row in session.exec(select(ActivityEvent)).all(): session.delete(row)
            for i, micros in enumerate((0, 500000, 999999)):
                session.add(ActivityEvent(traveler_id=2, actor_id=2, event_type='page_view', identifier='account',
                    outcome='success', occurred_at=datetime(2026, 11, 3, 21, 59, 59, micros, tzinfo=timezone.utc)))
            session.add(ActivityEvent(traveler_id=2, actor_id=1, actor_kind='staff', event_type='status_change',
                identifier='admin:1:status:active', outcome='success', occurred_at=datetime(2026, 11, 3, 22, tzinfo=timezone.utc)))
            session.add(ActivityEvent(traveler_id=3, actor_id=3, event_type='page_view', identifier='passport', outcome='success'))
            session.commit()
        yield client, app, headers


def test_history_admin_authorization_second_inclusive_beirut_and_stable_pages(api):
    client, _, headers = api
    path = '/admin/users/2/activity'
    assert client.get(path).status_code == 401
    assert client.get(path, headers=headers[1]).status_code == 403
    assert client.get('/admin/users/999/activity', headers=headers[0]).status_code == 404
    params = {'from_at': '2026-11-03T23:59:59', 'to_at': '2026-11-03T23:59:59', 'page_size': 2}
    first = client.get(path, headers=headers[0], params=params)
    assert first.status_code == 200, first.text
    assert first.headers['cache-control'] == 'no-store'
    assert first.json()['total'] == 3
    assert all(row['beirut_time'].startswith('2026-11-03T23:59:59') for row in first.json()['items'])
    second = client.get(path, headers=headers[0], params={**params, 'page': 2}).json()
    assert len(second['items']) == 1
    assert set(row['id'] for row in first.json()['items']).isdisjoint(row['id'] for row in second['items'])
    assert first.json()['items'][0]['occurred_at'].endswith('Z')
    assert client.get(path, headers=headers[0], params={'page_size': 101}).status_code == 422
    assert client.get(path, headers=headers[0], params={'from_at': '2026-11-04', 'to_at': '2026-11-03'}).status_code == 422


def test_documents_share_canonical_activity_and_are_owner_scoped(api):
    client, app, headers = api
    record_document_access(app.state.engine, app.state.activity_directory, owner_id=2,
                           upload_id='a' * 32, purpose='image_view')
    result = client.get('/activity/history?documents_only=true', headers=headers[1])
    assert result.status_code == 200, result.text
    assert result.json()['total'] == 1
    access = result.json()['items'][0]
    assert access['actor'] == 'You' and access['actor_id'] == 2
    assert access['resource_id'] == 'a' * 32
    assert set(access) == {'id', 'traveler_id', 'actor_id', 'actor', 'occurred_at', 'beirut_time',
                          'event_type', 'identifier', 'outcome', 'resource_type', 'resource_id', 'purpose'}
    admin = client.get('/admin/users/2/activity?documents_only=true', headers=headers[0]).json()
    assert admin['items'][0]['id'] == access['id']
    assert client.get('/activity/history?documents_only=true', headers=headers[2]).json()['items'] == []
    # Staff's own full history includes actions they took on another account.
    staff = client.get('/admin/users/1/activity', headers=headers[0]).json()
    assert any(row['event_type'] == 'status_change' and row['traveler_id'] == 2 for row in staff['items'])


def test_api_action_records_result_and_never_records_secret_payload(api):
    client, app, headers = api
    response = client.put('/profile', headers=headers[1], json={'surname': 'Changed synthetic name'})
    assert response.status_code == 200, response.text
    # Check a real authenticated operation regardless of its semantic validation.
    assert client.get('/sessions', headers=headers[1]).status_code == 200
    with Session(app.state.engine) as session:
        actions = session.exec(select(ActivityEvent).where(ActivityEvent.event_type == 'api_action')).all()
        assert actions
        assert any(row.outcome == 'success' for row in actions)
        assert 'Changed synthetic name' not in str(actions)


def test_dst_gap_is_rejected_and_fold_includes_both_hours():
    with pytest.raises(ValueError, match='does not exist'):
        boundary(datetime(2026, 3, 29, 0, 30))
    start = boundary(datetime(2026, 10, 24, 23, 30))
    end = boundary(datetime(2026, 10, 24, 23, 30), upper=True)
    assert (end - start).total_seconds() == 3601
    assert start.tzinfo == timezone.utc
