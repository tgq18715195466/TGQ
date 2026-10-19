"""Transactional outbox delivery, compensating deletion and reconciliation."""
from datetime import timedelta
import random

from sqlalchemy import delete, select, update

from app.models import (AnswerCitation, Chunk, Document, DocumentAsset, DocumentBlob, DocumentVersion,
                        EvalCase, EvalResult,
                        IngestJob, JobStep, Message, ParsedBlock, ParsedPage, ReconcileIssue,
                        RetrievalCandidate, RunEvent, SyncOutbox, now)


def backoff(attempt):
    return min(300, 2 ** min(attempt, 8)) + random.uniform(0, 1)


def deliver_outbox(db, item, deliver, max_retries=5):
    if item.status == 'DONE':
        return True
    try:
        deliver(item)
        item.status, item.processed_at, item.error_code = 'DONE', now(), None
        db.flush()
        return True
    except Exception:
        item.attempts += 1
        item.status = 'FAILED' if item.attempts >= max_retries else 'PENDING'
        item.available_at = now() + timedelta(seconds=backoff(item.attempts))
        item.error_code = 'VECTOR_SYNC_FAILED' if item.target == 'milvus' else 'GRAPH_SYNC_FAILED'
        db.flush()
        return False


def finish_deletion(db, tenant, document_id, hard):
    doc = db.scalar(select(Document).where(Document.tenant_id == tenant,
                    Document.id == document_id).with_for_update())
    if not doc:
        return True
    versions = db.scalars(select(DocumentVersion).where(DocumentVersion.tenant_id == tenant,
                           DocumentVersion.document_id == document_id)).all()
    version_ids = [v.id for v in versions]
    for version_id in version_ids:
        items = db.scalars(select(SyncOutbox).where(SyncOutbox.tenant_id == tenant,
                    SyncOutbox.aggregate_id == version_id, SyncOutbox.action == 'delete')).all()
        if {x.target for x in items if x.status == 'DONE'} != {'milvus', 'neo4j'}:
            return False
    doc.status, doc.current_version_id = 'DELETED', None
    db.execute(update(Chunk).where(Chunk.tenant_id == tenant,
               Chunk.document_id == document_id).values(deleted_at=now()))
    if not hard:
        return True
    # Hard deletion redacts historical snapshots and event payloads too. Soft deletion preserves history.
    runs = list(db.scalars(select(AnswerCitation.run_id).where(AnswerCitation.tenant_id == tenant,
                             AnswerCitation.document_id == document_id)).all())
    if runs:
        db.execute(delete(RunEvent).where(RunEvent.tenant_id == tenant, RunEvent.run_id.in_(runs)))
        db.execute(delete(AnswerCitation).where(AnswerCitation.tenant_id == tenant, AnswerCitation.run_id.in_(runs)))
        db.execute(delete(RetrievalCandidate).where(RetrievalCandidate.tenant_id == tenant,
                                                   RetrievalCandidate.run_id.in_(runs)))
        db.execute(update(Message).where(Message.tenant_id == tenant, Message.run_id.in_(runs))
                   .values(content='[内容已按文档硬删除策略清除]'))
    for result in db.scalars(select(EvalResult).where(EvalResult.tenant_id == tenant)):
        if document_id in result.prediction_jsonb.get('document_ids', []) or result.prediction_jsonb.get('query_run_id') in runs:
            result.prediction_jsonb = {'redacted': True}
    for case in db.scalars(select(EvalCase).where(EvalCase.tenant_id == tenant)):
        if case.data_jsonb.get('document_version') in version_ids:
            case.data_jsonb = {'id': case.case_key, 'redacted': True}
    jobs = list(db.scalars(select(IngestJob.id).where(IngestJob.tenant_id == tenant,
                                                    IngestJob.version_id.in_(version_ids))))
    db.execute(delete(JobStep).where(JobStep.tenant_id == tenant, JobStep.job_id.in_(jobs)))
    db.execute(delete(IngestJob).where(IngestJob.tenant_id == tenant, IngestJob.version_id.in_(version_ids)))
    for model in [ParsedBlock, ParsedPage, DocumentAsset, Chunk, ReconcileIssue]:
        db.execute(delete(model).where(model.tenant_id == tenant, model.version_id.in_(version_ids)))
    blobs = {v.original_blob_id for v in versions} | {v.normalized_pdf_blob_id for v in versions}
    db.execute(delete(DocumentVersion).where(DocumentVersion.tenant_id == tenant,
                                            DocumentVersion.id.in_(version_ids)))
    db.flush()
    for blob in blobs - {None}:
        referenced = db.scalar(select(DocumentVersion.id).where(DocumentVersion.tenant_id == tenant,
            (DocumentVersion.original_blob_id == blob) | (DocumentVersion.normalized_pdf_blob_id == blob)).limit(1))
        if not referenced:
            db.execute(delete(DocumentBlob).where(DocumentBlob.tenant_id == tenant, DocumentBlob.id == blob))
    # Keep completed outbox receipts for retry-safe remote deletion and audit; no document text.
    db.delete(doc)
    db.flush()
    return True


def reconcile_version(db, tenant, version, vector_ids, graph_ids):
    from app.repository import enqueue_sync
    expected = set(db.scalars(select(Chunk.id).where(Chunk.tenant_id == tenant,
                             Chunk.version_id == version.id, Chunk.deleted_at.is_(None))))
    doc = db.scalar(select(Document).where(Document.tenant_id == tenant, Document.id == version.document_id))
    deleting = doc is None or doc.deleted_at is not None
    if deleting:
        expected = set()
    actual = {'milvus': set(vector_ids), 'neo4j': set(graph_ids)}
    differences = {target: {'missing': sorted(expected - ids), 'extra': sorted(ids - expected)}
                   for target, ids in actual.items() if expected != ids}
    issue = db.scalar(select(ReconcileIssue).where(ReconcileIssue.tenant_id == tenant,
                      ReconcileIssue.version_id == version.id, ReconcileIssue.resolved_at.is_(None)))
    if differences:
        if not issue:
            issue = ReconcileIssue(tenant_id=tenant, version_id=version.id, detail_jsonb=differences)
            db.add(issue); db.flush()
        else:
            issue.detail_jsonb = differences
        for target in differences:
            item = enqueue_sync(db, tenant, version.id, target, 'delete' if deleting else 'upsert',
                                {'repair': True, 'document_id': version.document_id})
            # Full version replacement handles both missing and orphan IDs.
            item.status, item.attempts, item.available_at = 'PENDING', 0, now()
            item.payload_jsonb = {**item.payload_jsonb, 'repair': True}
        created = issue.created_at.replace(tzinfo=now().tzinfo) if issue.created_at.tzinfo is None else issue.created_at
        return {'version_id': version.id, 'differences': differences,
                'alert': now() - created > timedelta(minutes=15)}
    if issue:
        issue.resolved_at = now()
    return {'version_id': version.id, 'differences': {}, 'alert': False}
