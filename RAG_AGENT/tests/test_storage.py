from uuid import uuid4

import pytest
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import Session


def test_schema_has_tenant_and_no_blob_eager_loading():
    from app.models import Base, Document, DocumentBlob
    required = {'tenants', 'users', 'roles', 'user_roles', 'knowledge_bases', 'kb_members',
                'documents', 'document_versions', 'document_blobs', 'parsed_pages', 'parsed_blocks',
                'document_assets', 'chunks', 'ingest_jobs', 'job_steps', 'sync_outbox', 'chat_sessions',
                'messages', 'query_runs', 'retrieval_candidates', 'answer_citations', 'feedback',
                'eval_datasets', 'eval_cases', 'eval_runs', 'eval_results', 'audit_logs'}
    assert required <= set(Base.metadata.tables)
    for name, table in Base.metadata.tables.items():
        if name != 'tenants':
            assert 'tenant_id' in table.c, name
    assert not inspect(Document).relationships
    assert inspect(DocumentBlob).attrs.data.deferred


@pytest.fixture
def db():
    from app.models import Base
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session


def test_atomic_publish_requires_both_stores(db):
    from app.models import Document, DocumentVersion, SyncOutbox, Chunk
    from app.repository import publish_version
    tenant, kb, doc_id, old, new = [str(uuid4()) for _ in range(5)]
    db.add(Document(id=doc_id, tenant_id=tenant, kb_id=kb, name='Policy', current_version_id=old))
    db.add(DocumentVersion(id=new, tenant_id=tenant, document_id=doc_id, version_no=2, quality_status='PASS'))
    db.add(Chunk(tenant_id=tenant, kb_id=kb, document_id=doc_id, version_id=new,
                 chunk_no=0, chunk_text='Evidence', embedding_text='Evidence'))
    for target in ['milvus', 'neo4j']:
        db.add(SyncOutbox(tenant_id=tenant, aggregate_id=new, aggregate_type='version', target=target,
                          action='upsert', status='DONE' if target == 'milvus' else 'PENDING'))
    db.commit()
    with pytest.raises(ValueError, match='INDEX_NOT_READY'):
        publish_version(db, tenant, new)
    assert db.get(Document, doc_id).current_version_id == old
    item = db.scalar(select(SyncOutbox).where(SyncOutbox.target == 'neo4j'))
    item.status = 'DONE'
    db.commit()
    publish_version(db, tenant, new)
    assert db.get(Document, doc_id).current_version_id == new
    assert db.get(DocumentVersion, new).status == 'READY'


def test_blob_deduplication_is_per_tenant(db):
    from app.repository import save_blob
    tenant1, tenant2 = str(uuid4()), str(uuid4())
    a = save_blob(db, tenant1, b'file', 'text/plain', 'original')
    b = save_blob(db, tenant1, b'file', 'text/plain', 'original')
    c = save_blob(db, tenant2, b'file', 'text/plain', 'original')
    assert a.id == b.id
    assert a.id != c.id


def test_unauthorized_kb_is_denied(db):
    from app.models import KnowledgeBase, KBMember
    from app.repository import require_kb
    tenant, outsider, user, kb = [str(uuid4()) for _ in range(4)]
    db.add(KnowledgeBase(id=kb, tenant_id=tenant, name='private'))
    db.add(KBMember(tenant_id=tenant, kb_id=kb, user_id=user, role='reader'))
    db.commit()
    require_kb(db, tenant, user, kb)
    with pytest.raises(PermissionError):
        require_kb(db, tenant, outsider, kb)
    with pytest.raises(PermissionError):
        require_kb(db, outsider, user, kb)
    with pytest.raises(PermissionError):
        require_kb(db, tenant, user, kb, write=True)


def test_idempotency_rejects_changed_payload(db):
    from app.repository import idempotent
    tenant, user = str(uuid4()), str(uuid4())
    calls = []
    def work():
        calls.append(1)
        return {'id': 'result'}
    assert idempotent(db, tenant, user, 'upload', 'key', b'a', work) == {'id': 'result'}
    assert idempotent(db, tenant, user, 'upload', 'key', b'a', work) == {'id': 'result'}
    assert calls == [1]
    with pytest.raises(ValueError, match='IDEMPOTENCY_CONFLICT'):
        idempotent(db, tenant, user, 'upload', 'key', b'b', work)
