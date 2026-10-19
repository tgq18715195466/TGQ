"""Username/password login must gate the workspace and Agent API."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.db import transaction
from app.models import Base, LoginCredential, Tenant, User
from app.password_auth import AUTH_TENANT_ID, hash_password, set_login_password, verify_password


def test_password_hash_is_salted_and_verified():
    first = hash_password('test-password-123')
    second = hash_password('test-password-123')
    assert first != second
    assert 'test-password-123' not in first
    assert verify_password('test-password-123', first)
    assert not verify_password('wrong-password', first)


@pytest.fixture
def login_api():
    from app.api import create_app

    bind = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(bind)
    config = Settings(jwt_secret='test-only-secret-is-at-least-32-bytes')
    tenant, user = str(uuid4()), str(uuid4())
    with transaction(tenant, bind=bind) as db:
        db.add(Tenant(id=tenant, name='test'))
        db.add(User(id=user, tenant_id=tenant, name='管理员'))
    with transaction(AUTH_TENANT_ID, bind=bind) as db:
        set_login_password(db, 'admin', 'test-password-123', tenant, user)
    with TestClient(create_app(config=config, bind=bind)) as client:
        yield client, bind, tenant, user


def test_login_requires_password_before_agent_access(login_api):
    client, _, tenant, user = login_api
    assert client.get('/api/v1/chat/sessions').status_code == 401
    response = client.post('/api/v1/auth/login', json={'username': 'admin', 'password': 'test-password-123'})
    assert response.status_code == 200
    assert set(response.json()) == {'access_token', 'token_type', 'expires_in'}
    token = response.json()['access_token']
    profile = client.get('/api/v1/me', headers={'Authorization': f'Bearer {token}'})
    assert profile.status_code == 200
    assert profile.json()['id'] == user and profile.json()['tenant_id'] == tenant
    assert client.get('/api/v1/chat/sessions', headers={'Authorization': f'Bearer {token}'}).status_code == 200


def test_login_errors_are_generic_and_do_not_return_password_hash(login_api):
    client, *_ = login_api
    bad = client.post('/api/v1/auth/login', json={'username': 'admin', 'password': 'wrong-password'})
    unknown = client.post('/api/v1/auth/login', json={'username': 'missing', 'password': 'wrong-password'})
    assert bad.status_code == unknown.status_code == 401
    assert bad.json()['message'] == unknown.json()['message']
    assert 'password' not in str(bad.json()).lower()


def test_login_rate_limit_rejects_before_password_hash(login_api, monkeypatch):
    import app.api as api_module

    calls = []
    monkeypatch.setattr(api_module, 'verify_password', lambda password, encoded: calls.append(password) or False)
    client, *_ = login_api
    for _ in range(12):
        assert client.post('/api/v1/auth/login', json={'username': 'missing', 'password': 'wrong-password'}).status_code == 401
    limited = client.post('/api/v1/auth/login', json={'username': 'missing', 'password': 'wrong-password'})
    assert limited.status_code == 429
    assert len(calls) == 12


def test_repeated_failures_throttle_bad_passwords_but_not_the_owner(login_api):
    client, bind, tenant, user = login_api
    for _ in range(5):
        assert client.post('/api/v1/auth/login', json={'username': 'admin', 'password': 'wrong-password'}).status_code == 401
    with transaction(AUTH_TENANT_ID, bind=bind) as db:
        credential = db.scalar(select(LoginCredential).where(LoginCredential.username == 'admin'))
        assert credential.locked_until is not None
    assert client.post('/api/v1/auth/login', json={'username': 'admin', 'password': 'test-password-123'}).status_code == 200
    with transaction(AUTH_TENANT_ID, bind=bind) as db:
        set_login_password(db, 'admin', 'new-test-password-123', tenant, user)
    assert client.post('/api/v1/auth/login', json={'username': 'admin', 'password': 'new-test-password-123'}).status_code == 200


def test_expired_lock_starts_a_fresh_attempt_window(login_api):
    client, bind, *_ = login_api
    with transaction(AUTH_TENANT_ID, bind=bind) as db:
        credential = db.scalar(select(LoginCredential).where(LoginCredential.username == 'admin'))
        credential.failed_attempts = 5
        credential.locked_until = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert client.post('/api/v1/auth/login', json={'username': 'admin', 'password': 'wrong-password'}).status_code == 401
    assert client.post('/api/v1/auth/login', json={'username': 'admin', 'password': 'test-password-123'}).status_code == 200


def test_inactive_user_cannot_login(login_api):
    client, bind, tenant, user = login_api
    with transaction(tenant, bind=bind) as db:
        db.get(User, user).status = 'DISABLED'
    assert client.post('/api/v1/auth/login', json={'username': 'admin', 'password': 'test-password-123'}).status_code == 401
