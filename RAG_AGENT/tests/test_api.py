import time
from uuid import uuid4

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.db import transaction
from app.models import Base, Tenant, User, ChatSession, QueryRun


@pytest.fixture
def api():
    from app.api import create_app
    bind = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(bind)
    config = Settings(jwt_secret='test-only-secret-is-at-least-32-bytes')
    app = create_app(config=config, bind=bind)
    tenant, user = str(uuid4()), str(uuid4())
    with transaction(tenant, bind=bind) as db:
        db.add(Tenant(id=tenant, name='test'))
        db.add(User(id=user, tenant_id=tenant, name='user'))
    token = jwt.encode({'sub': user, 'tenant_id': tenant, 'iss': config.jwt_issuer,
                        'aud': config.jwt_audience, 'exp': int(time.time()) + 600}, config.jwt_secret,
                       algorithm='HS256')
    with TestClient(app) as client:
        client.headers['Authorization'] = 'Bearer ' + token
        yield client, bind, tenant, user


def test_auth_required_and_safe_errors(api):
    client, *_ = api
    response = client.get('/api/v1/knowledge-bases', headers={'Authorization': 'Bearer invalid'})
    assert response.status_code == 401
    assert {'request_id', 'error_code', 'message', 'retryable'} <= response.json().keys()
    assert 'Traceback' not in response.text


def test_workspace_lists_enforce_document_acl_and_session_owner(api):
    from app.models import Document, Message
    client, bind, tenant, user = api
    assert client.get('/api/v1/me').json()['id'] == user
    kb = client.post('/api/v1/knowledge-bases', json={'name': 'workspace'}).json()['id']
    upload = client.post(f'/api/v1/kbs/{kb}/documents',
                         files={'file': ('visible.txt', b'visible', 'text/plain')},
                         headers={'Idempotency-Key': 'workspace'}).json()
    rows = client.get(f'/api/v1/kbs/{kb}/documents').json()
    assert rows[0]['job']['id'] == upload['job_id']
    assert rows[0]['latest_version']['id'] == upload['version_id']
    with transaction(tenant, bind=bind) as db:
        db.get(Document, upload['document_id']).acl_jsonb = {'user_ids': [str(uuid4())]}
        other = ChatSession(tenant_id=tenant, user_id=str(uuid4()), title='private')
        db.add(other); db.flush()
        private_id = other.id
    assert client.get(f'/api/v1/kbs/{kb}/documents').json() == []
    assert client.get(f'/api/v1/kbs/{uuid4()}/documents').status_code == 403
    session = client.post('/api/v1/chat/sessions', json={'title': 'mine'}).json()
    with transaction(tenant, bind=bind) as db:
        db.add(Message(tenant_id=tenant, session_id=session['id'], role='user', content='hello'))
    assert [x['id'] for x in client.get('/api/v1/chat/sessions').json()] == [session['id']]
    assert client.get(f'/api/v1/chat/sessions/{private_id}/messages').status_code == 403
    history = client.get(f'/api/v1/chat/sessions/{session["id"]}/messages').json()
    assert history['messages'][0]['content'] == 'hello'
    assert history['runs'] == history['citations'] == []
    assert client.get('/api/v1/eval-datasets').json() == []
    assert client.get('/api/v1/eval-runs').json() == []
    status = client.get('/api/v1/system/status').json()
    assert status['api'] == 'ok' and status['external_services_verified'] is False
    assert 'jwt_secret' not in status


def test_history_rechecks_cited_document_acl(api):
    from app.models import AnswerCitation, Document
    client, bind, tenant, user = api
    kb = client.post('/api/v1/knowledge-bases', json={'name': 'sources'}).json()['id']
    upload = client.post(f'/api/v1/kbs/{kb}/documents',
                         files={'file': ('source.txt', b'source evidence', 'text/plain')},
                         headers={'Idempotency-Key': 'history-source'}).json()
    session = client.post('/api/v1/chat/sessions', json={'title': 'history'}).json()
    with transaction(tenant, bind=bind) as db:
        run = QueryRun(tenant_id=tenant, user_id=user, session_id=session['id'],
                       question='source?', kb_ids=[kb], status='COMPLETED')
        db.add(run); db.flush()
        db.add(AnswerCitation(tenant_id=tenant, run_id=run.id, document_id=upload['document_id'],
            document_version=upload['version_id'], document_name='source.txt', chunk_id=str(uuid4()),
            page_start=1, page_end=1, source_excerpt='source evidence'))
    url = f'/api/v1/chat/sessions/{session["id"]}/messages'
    assert client.get(url).json()['citations'][0]['source_excerpt'] == 'source evidence'
    with transaction(tenant, bind=bind) as db:
        db.get(Document, upload['document_id']).acl_jsonb = {'user_ids': [str(uuid4())]}
    assert client.get(url).status_code == 403


def test_eval_list_excludes_other_users_and_revoked_admin(api):
    from app.models import EvalDataset, EvalRun, KBMember
    from sqlalchemy import select
    client, bind, tenant, user = api
    kb = client.post('/api/v1/knowledge-bases', json={'name': 'evaluation'}).json()['id']
    with transaction(tenant, bind=bind) as db:
        dataset = EvalDataset(tenant_id=tenant, name='test-only', version='v1', sha256='0' * 64)
        db.add(dataset); db.flush()
        dataset_id = dataset.id
        private = EvalRun(tenant_id=tenant, dataset_id=dataset.id, user_id=str(uuid4()), config_jsonb={'kb_ids': [kb]})
        db.add(private)
    own = client.post('/api/v1/eval-runs', json={'dataset_id': dataset_id, 'kb_ids': [kb]}).json()
    assert [row['id'] for row in client.get('/api/v1/eval-runs').json()] == [own['id']]
    with transaction(tenant, bind=bind) as db:
        member = db.scalar(select(KBMember).where(KBMember.kb_id == kb, KBMember.user_id == user))
        member.role = 'reader'
    assert client.get('/api/v1/eval-runs').json() == []


def test_create_kb_upload_idempotency_and_scope(api):
    client, bind, tenant, user = api
    kb = client.post('/api/v1/knowledge-bases', json={'name': '合同'}).json()['id']
    files = {'file': ('contract.txt', b'contract A-001', 'text/plain')}
    response = client.post(f'/api/v1/kbs/{kb}/documents', files=files,
                           headers={'Idempotency-Key': 'upload-1'})
    assert response.status_code == 202, response.text
    body = response.json()
    assert client.post(f'/api/v1/kbs/{kb}/documents', files=files,
                       headers={'Idempotency-Key': 'upload-1'}).json() == body
    assert client.get(f'/api/v1/documents/{body["document_id"]}/download').content == b'contract A-001'
    assert client.post(f'/api/v1/kbs/{uuid4()}/documents', files=files,
                       headers={'Idempotency-Key': 'upload-2'}).status_code == 403


def test_sse_replays_only_after_last_id(api):
    from app.repository import append_event
    client, bind, tenant, user = api
    with transaction(tenant, bind=bind) as db:
        session = ChatSession(tenant_id=tenant, user_id=user)
        db.add(session); db.flush()
        run = QueryRun(tenant_id=tenant, user_id=user, session_id=session.id, question='hi', status='COMPLETED')
        db.add(run); db.flush()
        append_event(db, tenant, run.id, 'run.started', {})
        append_event(db, tenant, run.id, 'answer.delta', {'text': 'hi'})
        append_event(db, tenant, run.id, 'run.completed', {})
        run_id = run.id
    response = client.get(f'/api/v1/chat/runs/{run_id}/events', headers={'Last-Event-ID': '1'})
    assert response.status_code == 200
    assert 'id: 1\n' not in response.text
    assert response.text.index('id: 2') < response.text.index('id: 3')
    assert client.get(f'/api/v1/chat/runs/{uuid4()}/events').status_code == 403


def test_missing_idempotency_and_fake_mime_rejected(api):
    client, *_ = api
    kb = client.post('/api/v1/knowledge-bases', json={'name': 'secure'}).json()['id']
    response = client.post(f'/api/v1/kbs/{kb}/documents',
                           files={'file': ('fake.pdf', b'MZevil', 'application/pdf')},
                           headers={'Idempotency-Key': 'bad'})
    assert response.status_code == 400


def test_retry_cannot_jump_over_failed_malware_scan(api):
    from app.models import IngestJob
    client, bind, tenant, user = api
    kb = client.post('/api/v1/knowledge-bases', json={'name': 'safe'}).json()['id']
    upload = client.post(f'/api/v1/kbs/{kb}/documents', files={'file': ('a.txt', b'text', 'text/plain')},
                          headers={'Idempotency-Key': 'scan-test'}).json()
    with transaction(tenant, bind=bind) as db:
        job = db.get(IngestJob, upload['job_id'])
        job.status, job.current_stage, job.error_code = 'FAILED', 'validate_file', 'MALWARE_SCAN_REJECTED'
    response = client.post(f'/api/v1/document-versions/{upload["version_id"]}/retry',
                           json={'stage': 'normalize_file'}, headers={'Idempotency-Key': 'retry-bypass'})
    assert response.status_code == 400
    response = client.post(f'/api/v1/kbs/{kb}/documents',
                           files={'file': ('ok.txt', b'text', 'text/plain')})
    assert response.status_code == 400
