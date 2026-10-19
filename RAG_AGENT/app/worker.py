"""Tenant-scoped PostgreSQL worker. Redis loss cannot lose work or durable events."""
import argparse
from contextlib import contextmanager
from datetime import timedelta
import hashlib
import logging
import time
from uuid import UUID

from sqlalchemy import select, text

from app.adapters import Services
from app.config import settings
from app.db import engine, transaction
from app.ingest_runner import IngestRunner
from app.models import Chunk, Document, DocumentVersion, EvalRun, IngestJob, QueryRun, SyncOutbox, now
from app.repository import append_event, scoped
from app.sync import backoff, deliver_outbox, finish_deletion, reconcile_version
from app.workflows import build_ingestion_graph, postgres_checkpointer

log = logging.getLogger(__name__)


@contextmanager
def advisory_lock(bind, identity):
    key = int.from_bytes(hashlib.sha256(identity.encode()).digest()[:8], 'big', signed=True)
    with bind.connect() as connection:
        locked = connection.execute(text('SELECT pg_try_advisory_lock(:key)'), {'key': key}).scalar()
        try:
            yield locked
        finally:
            if locked:
                connection.execute(text('SELECT pg_advisory_unlock(:key)'), {'key': key})


def run_ingestion(tenant, job_id, config, bind):
    services = Services(config)
    try:
        with transaction(tenant, bind=bind) as db:
            job = scoped(db, IngestJob, tenant, job_id)
            state = {'tenant_id': tenant, 'job_id': job_id, 'start_stage': job.current_stage}
            thread = f'{tenant}:ingest:{job_id}:{job.config_version}'
        with postgres_checkpointer(config, tenant) as saver:
            graph = build_ingestion_graph(IngestRunner(config, services, bind), saver)
            # Restart from persisted business stage; all effects are idempotent.
            graph.invoke(state, {'configurable': {'thread_id': thread}})
    except Exception as exc:
        code = getattr(exc, 'error_code', 'INTERNAL_ERROR')
        retryable = getattr(exc, 'retryable', False) or isinstance(exc, (TimeoutError, ConnectionError))
        with transaction(tenant, bind=bind) as db:
            job = scoped(db, IngestJob, tenant, job_id)
            if job:
                job.error_code, job.error_detail = code, type(exc).__name__
                job.retry_count += 1
                job.status = 'CANCELLED' if code == 'CANCELLED' or job.cancel_requested else (
                    'RETRYING' if retryable and job.retry_count < config.max_retries else 'FAILED')
                job.available_at = now() + timedelta(seconds=backoff(job.retry_count))
                version = scoped(db, DocumentVersion, tenant, job.version_id)
                if version and version.status != 'READY':
                    version.status = job.status
        log.error('ingest_failed tenant=%s job=%s error_code=%s', tenant, job_id, code)
    finally:
        services.close()


def process_outbox(tenant, identity, config, bind):
    services = Services(config)
    try:
        with transaction(tenant, bind=bind) as db:
            item = db.scalar(select(SyncOutbox).where(SyncOutbox.tenant_id == tenant,
                            SyncOutbox.id == identity).with_for_update())
            if not item or item.status == 'DONE':
                return
            version = scoped(db, DocumentVersion, tenant, item.aggregate_id)
            doc = scoped(db, Document, tenant, version.document_id) if version else None
            if item.action == 'upsert' and (not doc or doc.deleted_at):
                item.status = 'CANCELLED'
                return
            def deliver(row):
                if row.action == 'purge':
                    store = services.vectors if row.target == 'milvus' else services.graph
                    store.purge(tenant, row.payload_jsonb['chunk_ids'])
                elif row.action == 'delete':
                    method = services.delete_vectors if row.target == 'milvus' else services.delete_graph
                    method(tenant, row.aggregate_id, row.id)
                else:
                    chunks = db.scalars(select(Chunk).where(Chunk.tenant_id == tenant,
                                       Chunk.version_id == version.id).order_by(Chunk.chunk_no)).all()
                    if row.payload_jsonb.get('repair'):
                        method = services.delete_vectors if row.target == 'milvus' else services.delete_graph
                        method(tenant, version.id, row.id)
                    method = services.sync_vectors if row.target == 'milvus' else services.sync_graph
                    method(tenant, version.id, chunks, [doc], row.id)
            delivered = deliver_outbox(db, item, deliver, config.max_retries)
            if delivered and item.action == 'delete' and doc:
                finish_deletion(db, tenant, doc.id, item.payload_jsonb.get('hard', False))
    finally:
        services.close()


def tick(tenant, config, bind):
    with transaction(tenant, bind=bind) as db:
        jobs = [(j.id, v.document_id) for j, v in db.execute(select(IngestJob, DocumentVersion).join(
            DocumentVersion, IngestJob.version_id == DocumentVersion.id).where(IngestJob.tenant_id == tenant,
            DocumentVersion.tenant_id == tenant, IngestJob.status.not_in(['READY', 'FAILED', 'CANCELLED', 'DELETED']),
            IngestJob.available_at <= now()).order_by(IngestJob.created_at).limit(50))]
        runs = list(db.scalars(select(QueryRun.id).where(QueryRun.tenant_id == tenant,
                    QueryRun.status.in_(['QUEUED', 'RUNNING'])).order_by(QueryRun.created_at).limit(50)))
        outbox = [(x.id, x.payload_jsonb.get('document_id') or (scoped(db, DocumentVersion, tenant, x.aggregate_id).document_id
                    if scoped(db, DocumentVersion, tenant, x.aggregate_id) else x.aggregate_id))
            for x in db.scalars(select(SyncOutbox).where(SyncOutbox.tenant_id == tenant,
                SyncOutbox.status == 'PENDING', SyncOutbox.available_at <= now()).limit(50))]
        evals = list(db.scalars(select(EvalRun.id).where(EvalRun.tenant_id == tenant,
                                EvalRun.status.in_(['QUEUED', 'RUNNING'])).limit(10)))
    for identity in runs:
        with advisory_lock(bind, f'query:{identity}') as locked:
            if locked:
                try:
                    from app.query import process_query
                    with postgres_checkpointer(config, tenant) as saver:
                        process_query(tenant, identity, config, bind, saver)
                except Exception as exc:
                    with transaction(tenant, bind=bind) as db:
                        run = scoped(db, QueryRun, tenant, identity)
                        if run and run.status not in {'CANCELLED', 'COMPLETED'}:
                            run.status, run.error_code = 'FAILED', 'FORBIDDEN_SCOPE' if isinstance(exc, PermissionError) else 'INTERNAL_ERROR'
                            append_event(db, tenant, identity, 'run.failed', {'error_code': run.error_code})
                return True
    for identity, document_id in jobs:
        with advisory_lock(bind, f'document:{document_id}') as locked:
            if locked:
                run_ingestion(tenant, identity, config, bind)
                return True
    for identity, document_id in outbox:
        with advisory_lock(bind, f'document:{document_id}') as locked:
            if locked:
                process_outbox(tenant, identity, config, bind)
                return True
    for identity in evals:
        with advisory_lock(bind, f'eval:{identity}') as locked:
            if locked:
                from app.evaluation import process_eval
                process_eval(tenant, identity, config, bind)
                return True
    return False


def reconcile(tenant, config, bind):
    services = Services(config)
    reports = []
    try:
        with transaction(tenant, bind=bind) as db:
            versions = [(v.id, v.document_id) for v in db.scalars(select(DocumentVersion).where(
                DocumentVersion.tenant_id == tenant, DocumentVersion.status.in_(['READY', 'DELETING', 'DELETED'])))]
        for identity, document_id in versions:
            with advisory_lock(bind, f'document:{document_id}') as locked:
                if locked:
                    vector_ids, graph_ids = services.vector_ids(tenant, identity), services.graph_ids(tenant, identity)
                    with transaction(tenant, bind=bind) as db:
                        version = scoped(db, DocumentVersion, tenant, identity)
                        if version:
                            reports.append(reconcile_version(db, tenant, version, vector_ids, graph_ids))
        # IDs absent from PostgreSQL have no version to reconcile, queue tenant-scoped purge receipts.
        with transaction(tenant, bind=bind) as db:
            expected = set(db.scalars(select(Chunk.id).where(Chunk.tenant_id == tenant)))
            for target, ids in [('milvus', services.vector_ids(tenant)), ('neo4j', services.graph_ids(tenant))]:
                orphaned = sorted(ids - expected)
                if orphaned:
                    from uuid import uuid5, NAMESPACE_URL
                    aggregate = str(uuid5(NAMESPACE_URL, f'{tenant}:{target}:orphans'))
                    from app.repository import enqueue_sync
                    item = enqueue_sync(db, tenant, aggregate, target, 'purge', {'chunk_ids': orphaned})
                    item.payload_jsonb, item.status, item.attempts = {'chunk_ids': orphaned}, 'PENDING', 0
                    reports.append({'orphan_target': target, 'count': len(orphaned), 'compensation': item.id})
    finally:
        services.close()
    return reports


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--tenant', required=True, type=UUID)
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--reconcile', action='store_true')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    config, bind, tenant = settings(), engine(), str(args.tenant)
    if args.reconcile:
        import json
        print(json.dumps(reconcile(tenant, config, bind), ensure_ascii=False))
        return
    last_reconcile = time.monotonic()
    while True:
        handled = tick(tenant, config, bind)
        if args.once:
            break
        if time.monotonic() - last_reconcile > 60:
            try:
                for report in reconcile(tenant, config, bind):
                    if report.get('alert'):
                        log.error('reconciliation_divergence tenant=%s report=%s', tenant, report)
            except Exception as exc:
                log.error('reconciler_unavailable tenant=%s error=%s', tenant, type(exc).__name__)
            last_reconcile = time.monotonic()
        if not handled:
            time.sleep(config.worker_poll_seconds)


if __name__ == '__main__':
    main()
