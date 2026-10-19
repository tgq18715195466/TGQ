"""Real PostgreSQL tests; opt in with TEST_POSTGRES_DSN (dedicated DB only)."""
import os
import subprocess
import sys
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url

from app.db import transaction
from app.models import (Base, Tenant, User, KnowledgeBase, KBMember, Document, DocumentVersion,
                        Chunk, ChatSession, QueryRun, Message, RunEvent, LoginCredential)
from app.password_auth import AUTH_TENANT_ID, set_login_password
from app.repository import append_event, save_blob, scoped

pytestmark = pytest.mark.integration


@pytest.fixture(scope='module')
def pg():
    dsn = os.getenv('TEST_POSTGRES_DSN')
    if not dsn:
        pytest.skip('Set TEST_POSTGRES_DSN to a dedicated PostgreSQL database')
    engine = create_engine(dsn)
    yield engine
    engine.dispose()


def test_role_is_not_rls_bypass(pg):
    with pg.connect() as conn:
        row = conn.execute(text('SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname=current_user')).one()
        assert row == (False, False)


def test_login_credentials_are_hidden_from_business_tenants(pg):
    tenant, user = str(uuid4()), str(uuid4())
    username = 'login-' + uuid4().hex[:12]
    with transaction(tenant, bind=pg) as db:
        db.add(Tenant(id=tenant, name='login-rls'))
        db.add(User(id=user, tenant_id=tenant, name='login-user'))
    with transaction(AUTH_TENANT_ID, bind=pg) as db:
        set_login_password(db, username, 'test-password-123', tenant, user)
        assert db.scalar(select(LoginCredential).where(LoginCredential.username == username)) is not None
    with transaction(tenant, bind=pg) as db:
        assert db.scalar(select(LoginCredential).where(LoginCredential.username == username)) is None
    with pg.connect() as conn:
        assert conn.execute(text('SELECT id FROM login_credentials WHERE username = :username'),
                            {'username': username}).all() == []


def test_document_api_pagination_filters_acl_before_offset(pg):
    import time
    import jwt
    from fastapi.testclient import TestClient
    from app.api import create_app
    from app.config import Settings
    tenant, user = str(uuid4()), str(uuid4())
    with transaction(tenant, bind=pg) as db:
        db.add(Tenant(id=tenant, name='workspace-pagination')); db.flush()
        db.add(User(id=user, tenant_id=tenant, name='owner')); db.flush()
        kb = KnowledgeBase(tenant_id=tenant, name='paged', created_by=user)
        db.add(kb); db.flush()
        kb_id = kb.id
        db.add(KBMember(tenant_id=tenant, kb_id=kb.id, user_id=user, role='owner'))
        for index, acl in enumerate([{}, {'user_ids': [user]}, {'user_ids': []},
                                     {'user_ids': None}, {'user_ids': [str(uuid4())]}]):
            db.add(Document(tenant_id=tenant, kb_id=kb.id, name=f'doc-{index}', acl_jsonb=acl))
    config = Settings(jwt_secret='workspace-test-secret-with-more-than-32-bytes')
    token = jwt.encode({'sub': user, 'tenant_id': tenant, 'iss': config.jwt_issuer,
                       'aud': config.jwt_audience, 'exp': int(time.time()) + 600},
                      config.jwt_secret, algorithm='HS256')
    with TestClient(create_app(config=config, bind=pg)) as client:
        client.headers['Authorization'] = 'Bearer ' + token
        names = []
        for offset in range(5):
            response = client.get(f'/api/v1/kbs/{kb_id}/documents?offset={offset}&limit=1')
            assert response.status_code == 200, response.text
            names.extend(row['name'] for row in response.json())
        assert set(names) == {'doc-0', 'doc-1', 'doc-2', 'doc-3'}
        assert len(names) == 4


def test_rls_and_composite_fk_stop_cross_tenant(pg):
    a, b = str(uuid4()), str(uuid4())
    with transaction(a, bind=pg) as db:
        db.add(Tenant(id=a, name='test-a'))
        db.flush()
        user = User(tenant_id=a, name='test-user')
        db.add(user)
        db.flush()
        user_id = user.id
    with transaction(b, bind=pg) as db:
        db.add(Tenant(id=b, name='test-b'))
        db.flush()
        assert db.scalars(select(User)).all() == []  # intentionally omits tenant filter; RLS blocks.
        kb = KnowledgeBase(tenant_id=b, name='b')
        db.add(kb)
        db.flush()
        with pytest.raises(Exception):
            with db.begin_nested():
                db.add(KBMember(tenant_id=b, kb_id=kb.id, user_id=user_id, role='reader'))
                db.flush()
    with pg.connect() as conn:
        assert conn.execute(text('SELECT id FROM users')).all() == []


def test_blob_fts_and_durable_events_after_connection_restart(pg):
    tenant = str(uuid4())
    with transaction(tenant, bind=pg) as db:
        db.add(Tenant(id=tenant, name='durable'))
        db.flush()
        user = User(tenant_id=tenant, name='user')
        kb = KnowledgeBase(tenant_id=tenant, name='kb')
        db.add_all([user, kb]); db.flush()
        blob = save_blob(db, tenant, b'evidence', 'text/plain', 'original')
        doc = Document(tenant_id=tenant, kb_id=kb.id, name='doc')
        db.add(doc); db.flush()
        version = DocumentVersion(tenant_id=tenant, document_id=doc.id, version_no=1,
                                  original_blob_id=blob.id)
        db.add(version); db.flush()
        chunk = Chunk(tenant_id=tenant, kb_id=kb.id, document_id=doc.id, version_id=version.id,
                      chunk_no=0, chunk_text='合同编号 ABC-123', embedding_text='合同编号 ABC-123',
                      search_tokens='合同 编号 ABC-123')
        session = ChatSession(tenant_id=tenant, user_id=user.id)
        db.add_all([chunk, session]); db.flush()
        assert db.execute(text("SELECT count(*) FROM chunks WHERE search_vector @@ plainto_tsquery('simple','合同')")).scalar() == 1
        run = QueryRun(tenant_id=tenant, session_id=session.id, user_id=user.id, question='编号?')
        db.add(run); db.flush()
        append_event(db, tenant, run.id, 'run.started', {})
        db.add(Message(tenant_id=tenant, session_id=session.id, run_id=run.id,
                       role='assistant', content='ABC-123'))
        run.status = 'COMPLETED'
        append_event(db, tenant, run.id, 'run.completed', {'status': 'COMPLETED'})
        run_id, blob_id = run.id, blob.id
    pg.dispose()
    with transaction(tenant, bind=pg) as db:
        assert scoped(db, QueryRun, tenant, run_id).status == 'COMPLETED'
        assert db.scalar(select(Message).where(Message.run_id == run_id)).content == 'ABC-123'
        from app.models import DocumentBlob
        assert scoped(db, DocumentBlob, tenant, blob_id).data == b'evidence'
        events = db.scalars(select(RunEvent).where(RunEvent.run_id == run_id,
                           RunEvent.sequence > 1).order_by(RunEvent.sequence)).all()
        assert [(x.sequence, x.event_type) for x in events] == [(2, 'run.completed')]


def test_migration_upgrade_and_rollback_in_isolated_schema(pg):
    schema = 'migration_test_' + uuid4().hex
    with pg.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA {schema}'))
    url = make_url(str(pg.url.render_as_string(hide_password=False))).update_query_dict(
        {'options': f'-csearch_path={schema}'})
    env = dict(os.environ, POSTGRES_DSN=url.render_as_string(hide_password=False))
    try:
        for args in [('upgrade', 'head'), ('downgrade', 'base'), ('upgrade', 'head')]:
            process = subprocess.run([sys.executable, '-m', 'alembic', *args], env=env,
                                     capture_output=True, text=True, timeout=60)
            assert process.returncode == 0, process.stderr[-2000:]
        with pg.connect() as conn:
            tables = conn.execute(text('SELECT tablename FROM pg_tables WHERE schemaname=:schema'),
                                  {'schema': schema}).scalars().all()
            assert set(Base.metadata.tables) <= set(tables)
    finally:
        with pg.begin() as conn:
            conn.execute(text(f'DROP SCHEMA {schema} CASCADE'))
