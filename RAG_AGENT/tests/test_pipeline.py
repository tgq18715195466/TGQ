"""Real PostgreSQL orchestration tests with EXPLICIT external-service doubles.

These do not certify MinerU/Milvus/Neo4j/model server integrations.
"""
import os
from uuid import uuid4
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select

from app.config import Settings
from app.db import transaction
from app.ingestion import IngestionError, adapt_mineru_response
from app.ingest_runner import IngestRunner
from app.models import (AnswerCitation, Chunk, Document, DocumentVersion, IngestJob, KBMember, KnowledgeBase,
                        QueryRun, ChatSession, RunEvent, Tenant, User)
from app.repository import save_blob
from app.workflows import build_ingestion_graph, postgres_checkpointer

pytestmark = pytest.mark.integration


class FakeServices:
    def __init__(self):
        self.rows, self.graph_rows, self.fail = {}, {}, False
        self.vectors = SimpleNamespace(search=lambda *args: [(key, .95) for key in self.rows])
        self.graph = SimpleNamespace(search=lambda *args: [(key, .9) for key in self.graph_rows])
        self.models = SimpleNamespace(rerank=lambda q, texts: [.95]*len(texts),
            generate=lambda q, evidence, summarize=False: ({'claims': [{'text': evidence[0]['chunk_text'],
                                                     'chunk_ids': [evidence[0]['chunk_id']]}]}, {'total_tokens': 1}))
    def embed(self, texts): return [[1.0]*3 for _ in texts]
    def extract_graph(self, chunks): return {'entities': [], 'relations': []}
    def sync_vectors(self, tenant, version, chunks, docs, receipt, embeddings=None):
        if self.fail: raise TimeoutError('simulated vector outage')
        self.rows.update({x.id: version for x in chunks})
    def sync_graph(self, tenant, version, chunks, docs, receipt, graph=None):
        self.graph_rows.update({x.id: version for x in chunks})
    def delete_vectors(self, tenant, version, receipt):
        self.rows = {k: v for k, v in self.rows.items() if v != version}
    def delete_graph(self, tenant, version, receipt):
        self.graph_rows = {k: v for k, v in self.graph_rows.items() if v != version}
    def close(self): pass


@pytest.fixture
def scenario(monkeypatch):
    dsn = os.getenv('TEST_POSTGRES_DSN')
    if not dsn:
        pytest.skip('Set TEST_POSTGRES_DSN; these tests use real PostgreSQL and explicit model/index doubles')
    bind = create_engine(dsn)
    config = Settings(postgres_dsn=dsn, development_skip_scanner=True, environment='development', embedding_dimension=3)
    tenant = str(uuid4())
    with transaction(tenant, bind=bind) as db:
        db.add(Tenant(id=tenant, name='pipeline-test')); db.flush()
        user, kb = User(tenant_id=tenant, name='user'), KnowledgeBase(tenant_id=tenant, name='test')
        db.add_all([user, kb]); db.flush()
        db.add(KBMember(tenant_id=tenant, kb_id=kb.id, user_id=user.id, role='owner')); db.flush()
        blob = save_blob(db, tenant, '合同金额为100元。'.encode(), 'text/plain', 'original')
        doc = Document(tenant_id=tenant, kb_id=kb.id, name='contract.txt', source_name='contract.txt', mime_type='text/plain')
        db.add(doc); db.flush()
        version = DocumentVersion(tenant_id=tenant, document_id=doc.id, version_no=1, original_blob_id=blob.id,
                                  parse_config_jsonb={'filename': 'contract.txt'})
        db.add(version); db.flush()
        job = IngestJob(tenant_id=tenant, version_id=version.id)
        db.add(job); db.flush()
        identities = {'tenant': tenant, 'user': user.id, 'kb': kb.id, 'doc': doc.id, 'version': version.id, 'job': job.id}
    async def parse(client, pdf, version_id):
        return adapt_mineru_response({'version': 'fixture', 'results': {'document': {
            'content_list': [{'type': 'text', 'text': '合同金额为100元。', 'page_idx': 0, 'confidence': .99}],
            'middle_json': {'pdf_info': [{}]}}}}, version_id)
    monkeypatch.setattr('app.ingest_runner.MinerUClient.parse', parse)
    yield bind, config, identities, FakeServices()
    bind.dispose()


def ingest(bind, config, ids, services, start='validate_file'):
    with postgres_checkpointer(config, ids['tenant']) as saver:
        graph = build_ingestion_graph(IngestRunner(config, services, bind), saver)
        graph.invoke({'tenant_id': ids['tenant'], 'job_id': ids['job'], 'start_stage': start},
                     {'configurable': {'thread_id': ids['tenant'] + ':pipeline:' + ids['job']}})


def test_persisted_ingestion_query_and_citations(scenario):
    from app.query import process_query
    bind, config, ids, services = scenario
    ingest(bind, config, ids, services)
    with transaction(ids['tenant'], bind=bind) as db:
        assert db.get(Document, ids['doc']).current_version_id == ids['version']
        assert db.get(IngestJob, ids['job']).status == 'READY'
        session = ChatSession(tenant_id=ids['tenant'], user_id=ids['user'])
        db.add(session); db.flush()
        run = QueryRun(tenant_id=ids['tenant'], user_id=ids['user'], session_id=session.id,
                       question='合同金额', kb_ids=[ids['kb']])
        db.add(run); db.flush(); run_id = run.id
    with postgres_checkpointer(config, ids['tenant']) as saver:
        process_query(ids['tenant'], run_id, config, bind, saver, services)
    with transaction(ids['tenant'], bind=bind) as db:
        assert db.get(QueryRun, run_id).status == 'COMPLETED'
        citation = db.scalar(select(AnswerCitation).where(AnswerCitation.run_id == run_id))
        assert citation.source_excerpt == '合同金额为100元。'
        events = db.scalars(select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.sequence)).all()
        assert events[-1].event_type == 'run.completed'
        assert len(set(x.sequence for x in events)) == len(events)


def test_failed_remote_sync_blocks_ready_and_restart_recovers(scenario):
    bind, config, ids, services = scenario
    services.fail = True
    with pytest.raises(IngestionError, match='VECTOR_SYNC_FAILED'):
        ingest(bind, config, ids, services)
    with transaction(ids['tenant'], bind=bind) as db:
        assert db.get(Document, ids['doc']).current_version_id is None
        assert db.get(DocumentVersion, ids['version']).status != 'READY'
    services.fail = False
    bind.dispose()
    ingest(bind, config, ids, services, 'sync_milvus')
    with transaction(ids['tenant'], bind=bind) as db:
        assert db.get(Document, ids['doc']).current_version_id == ids['version']
        ids_pg = set(db.scalars(select(Chunk.id).where(Chunk.version_id == ids['version'])))
        assert set(services.rows) == set(services.graph_rows) == ids_pg


def test_hard_delete_serializes_with_final_answer_commit(scenario, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    import app.query as query_module
    from app.models import Message, SyncOutbox
    from app.sync import finish_deletion
    bind, config, ids, services = scenario
    ingest(bind, config, ids, services)
    with transaction(ids['tenant'], bind=bind) as db:
        session = ChatSession(tenant_id=ids['tenant'], user_id=ids['user'])
        db.add(session); db.flush()
        run = QueryRun(tenant_id=ids['tenant'], session_id=session.id, user_id=ids['user'],
                       question='合同金额', kb_ids=[ids['kb']])
        db.add(run); db.flush(); run_id = run.id
    final_auth, delete_attempt, release = Event(), Event(), Event()
    original = query_module.authorized_chunks
    def pause_final(*args, **kwargs):
        import inspect
        rows = original(*args, **kwargs)
        if inspect.currentframe().f_back.f_code.co_name == 'persist_answer':
            final_auth.set()
            assert release.wait(10)
        return rows
    monkeypatch.setattr(query_module, 'authorized_chunks', pause_final)
    def delete_after_auth():
        assert final_auth.wait(10)
        delete_attempt.set()
        with transaction(ids['tenant'], bind=bind) as db:
            doc = db.scalar(select(Document).where(Document.id == ids['doc']).with_for_update())
            from app.models import now
            doc.deleted_at, doc.status = now(), 'DELETING'
            for target in ['milvus', 'neo4j']:
                db.add(SyncOutbox(tenant_id=ids['tenant'], aggregate_id=ids['version'], aggregate_type='version',
                                 target=target, action='delete', status='DONE', payload_jsonb={'hard': True}))
            db.flush()
            assert finish_deletion(db, ids['tenant'], ids['doc'], True)
    with ThreadPoolExecutor(2) as pool:
        answer = pool.submit(query_module.process_query, ids['tenant'], run_id, config, bind, None, services)
        deletion = pool.submit(delete_after_auth)
        assert final_auth.wait(10) and delete_attempt.wait(10)
        release.set()
        answer.result(20); deletion.result(20)
    with transaction(ids['tenant'], bind=bind) as db:
        assert db.scalar(select(AnswerCitation).where(AnswerCitation.run_id == run_id)) is None
        assert db.scalar(select(RunEvent).where(RunEvent.run_id == run_id)) is None
        assert '100' not in db.scalar(select(Message.content).where(Message.run_id == run_id))


def test_bm25_recall_respects_acl_and_deleted_chunks(scenario):
    from app.query import keyword_recall
    from app.models import now
    bind, config, ids, services = scenario
    ingest(bind, config, ids, services)
    with transaction(ids['tenant'], bind=bind) as db:
        args = (db, ids['tenant'], ids['user'], '合同金额如何查询', [ids['kb']], 10)
        assert keyword_recall(*args)
        doc = db.get(Document, ids['doc'])
        doc.acl_jsonb = {'user_ids': [str(uuid4())]}
        db.flush()
        assert keyword_recall(*args) == []
        doc.acl_jsonb = {}
        doc.current_version_id = None
        db.flush()
        assert keyword_recall(*args) == []
        doc.current_version_id = ids['version']
        doc.deleted_at = now()
        db.flush()
        assert keyword_recall(*args) == []
        doc.deleted_at = None
        chunk = db.scalar(select(Chunk).where(Chunk.version_id == ids['version']))
        chunk.deleted_at = now()
        db.flush()
        assert keyword_recall(*args) == []
        with pytest.raises(PermissionError):
            keyword_recall(db, ids['tenant'], ids['user'], '合同', [str(uuid4())], 10)
