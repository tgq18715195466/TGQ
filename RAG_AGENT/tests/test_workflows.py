from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models import Base, Document, DocumentVersion, Chunk, SyncOutbox


def test_outbox_retry_and_idempotent_delivery():
    from app.sync import deliver_outbox
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    tenant, version = str(uuid4()), str(uuid4())
    with Session(engine) as db:
        item = SyncOutbox(tenant_id=tenant, aggregate_type='version', aggregate_id=version,
                          target='milvus', action='upsert')
        db.add(item); db.commit()
        calls = []
        def failing(row):
            calls.append(row.id)
            raise TimeoutError()
        assert not deliver_outbox(db, item, failing)
        assert item.status == 'PENDING' and item.attempts == 1
        assert item.error_code == 'VECTOR_SYNC_FAILED'
        assert deliver_outbox(db, item, lambda row: calls.append(row.id))
        assert item.status == 'DONE'
        assert deliver_outbox(db, item, failing)
        assert len(calls) == 2


def test_ingestion_graph_has_required_nodes_and_keeps_only_ids():
    from app.workflows import INGEST_STAGES, build_ingestion_graph
    from langgraph.checkpoint.memory import InMemorySaver
    seen = []
    graph = build_ingestion_graph(lambda name, state: seen.append(name), InMemorySaver())
    state = {'tenant_id': str(uuid4()), 'job_id': str(uuid4()), 'start_stage': 'validate_file'}
    graph.invoke(state, {'configurable': {'thread_id': 'test'}})
    assert seen == INGEST_STAGES
    assert 'publish_version' in seen and seen[-1] == 'cleanup_temp_files'


def test_resume_at_requested_stage():
    from app.workflows import INGEST_STAGES, build_ingestion_graph
    seen = []
    graph = build_ingestion_graph(lambda name, state: seen.append(name))
    graph.invoke({'tenant_id': str(uuid4()), 'job_id': str(uuid4()), 'start_stage': 'parse_with_mineru'})
    assert seen[0] == 'parse_with_mineru'
    assert seen == INGEST_STAGES[INGEST_STAGES.index('parse_with_mineru'):]


def test_hard_delete_waits_for_remote_deletions():
    from app.sync import finish_deletion
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    tenant, doc_id, version_id, kb = [str(uuid4()) for _ in range(4)]
    with Session(engine) as db:
        doc = Document(id=doc_id, tenant_id=tenant, kb_id=kb, name='delete', status='DELETING')
        version = DocumentVersion(id=version_id, tenant_id=tenant, document_id=doc_id, version_no=1)
        db.add_all([doc, version]); db.flush()
        db.add(Chunk(tenant_id=tenant, kb_id=kb, document_id=doc_id, version_id=version_id,
                     chunk_no=0, chunk_text='private', embedding_text='private'))
        for target in ('milvus', 'neo4j'):
            db.add(SyncOutbox(tenant_id=tenant, aggregate_type='version', aggregate_id=version_id,
                target=target, action='delete', payload_jsonb={'hard': True},
                status='DONE' if target == 'milvus' else 'PENDING'))
        db.commit()
        assert not finish_deletion(db, tenant, doc_id, True)
        assert db.get(Document, doc_id) is not None
        pending = db.scalar(select(SyncOutbox).where(SyncOutbox.target == 'neo4j'))
        pending.status = 'DONE'; db.commit()
        assert finish_deletion(db, tenant, doc_id, True)
        assert db.scalar(select(Chunk)) is None
        assert db.get(Document, doc_id) is None
