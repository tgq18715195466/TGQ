import hashlib

from sqlalchemy import select, func, text

from app.models import (AuditLog, Chunk, Document, DocumentBlob, DocumentVersion,
                        IdempotencyRecord, KBMember, KnowledgeBase, QueryRun, RunEvent, SyncOutbox)


def scoped(db, model, tenant, identity):
    return db.scalar(select(model).where(model.tenant_id == tenant, model.id == identity))


def lock_key(db, value):
    if db.bind.dialect.name == 'postgresql':
        key = int.from_bytes(hashlib.sha256(value.encode()).digest()[:8], 'big', signed=True)
        db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': key})


def save_blob(db, tenant, data, mime, role):
    digest = hashlib.sha256(data).hexdigest()
    lock_key(db, f'blob:{tenant}:{digest}:{role}')
    blob = db.scalar(select(DocumentBlob).where(DocumentBlob.tenant_id == tenant,
                     DocumentBlob.sha256 == digest, DocumentBlob.blob_role == role))
    if blob is None:
        blob = DocumentBlob(tenant_id=tenant, sha256=digest, mime_type=mime,
                            blob_role=role, size_bytes=len(data), data=data)
        db.add(blob)
        db.flush()
    return blob


def require_kb(db, tenant, user, kb_id, write=False, admin=False):
    kb = scoped(db, KnowledgeBase, tenant, kb_id)
    member = db.scalar(select(KBMember).where(KBMember.tenant_id == tenant,
                       KBMember.kb_id == kb_id, KBMember.user_id == user))
    allowed = {'owner'} if admin else {'owner', 'editor'} if write else {'owner', 'editor', 'reader'}
    if kb is None or member is None or member.role not in allowed:
        raise PermissionError('FORBIDDEN_SCOPE')
    return kb


def document_allowed(document, user):
    allowed_users = (document.acl_jsonb or {}).get('user_ids', [])
    return not allowed_users or user in allowed_users


def require_document(db, tenant, user, document_id, write=False, include_deleted=False):
    doc = scoped(db, Document, tenant, document_id)
    if not doc or (doc.deleted_at and not include_deleted) or not document_allowed(doc, user):
        raise PermissionError('FORBIDDEN_SCOPE')
    require_kb(db, tenant, user, doc.kb_id, write=write)
    return doc


def authorized_chunks(db, tenant, user, kb_ids, chunk_ids=None):
    for kb in kb_ids:
        require_kb(db, tenant, user, kb)
    stmt = select(Chunk, Document).join(Document, (Document.id == Chunk.document_id)
                & (Document.tenant_id == Chunk.tenant_id)).where(
        Chunk.tenant_id == tenant, Chunk.kb_id.in_(kb_ids), Chunk.deleted_at.is_(None),
        Document.deleted_at.is_(None), Document.current_version_id == Chunk.version_id)
    if chunk_ids is not None:
        stmt = stmt.where(Chunk.id.in_(chunk_ids))
    return [(chunk, doc) for chunk, doc in db.execute(stmt) if document_allowed(doc, user)]


def publish_version(db, tenant, version_id):
    version = scoped(db, DocumentVersion, tenant, version_id)
    if not version:
        raise ValueError('VERSION_NOT_FOUND')
    doc = db.scalar(select(Document).where(Document.tenant_id == tenant,
                    Document.id == version.document_id).with_for_update())
    if not doc or doc.deleted_at or doc.status in {'DELETING', 'DELETED'}:
        raise ValueError('DOCUMENT_DELETED')
    items = db.scalars(select(SyncOutbox).where(SyncOutbox.tenant_id == tenant,
                       SyncOutbox.aggregate_id == version_id, SyncOutbox.action == 'upsert')).all()
    count = db.scalar(select(func.count()).select_from(Chunk).where(
        Chunk.tenant_id == tenant, Chunk.version_id == version_id, Chunk.deleted_at.is_(None)))
    if version.quality_status not in {'PASS', 'WARN'} or not count or {x.target for x in items if x.status == 'DONE'} != {'milvus', 'neo4j'} \
            or any(x.status != 'DONE' for x in items):
        raise ValueError('INDEX_NOT_READY')
    if doc.current_version_id:
        current = scoped(db, DocumentVersion, tenant, doc.current_version_id)
        if current and current.version_no > version.version_no:
            version.status = 'SUPERSEDED'
            db.flush()
            return
    doc.current_version_id, doc.status = version_id, 'READY'
    version.status = 'READY'
    db.flush()


def idempotent(db, tenant, user, operation, key, payload, action):
    if not key or len(key) > 200:
        raise ValueError('IDEMPOTENCY_KEY_REQUIRED')
    digest = hashlib.sha256(payload).hexdigest()
    lock_key(db, f'idem:{tenant}:{user}:{operation}:{key}')
    existing = db.scalar(select(IdempotencyRecord).where(IdempotencyRecord.tenant_id == tenant,
        IdempotencyRecord.user_id == user, IdempotencyRecord.operation == operation,
        IdempotencyRecord.key == key))
    if existing:
        if existing.request_hash != digest:
            raise ValueError('IDEMPOTENCY_CONFLICT')
        return existing.response_jsonb
    result = action()
    db.add(IdempotencyRecord(tenant_id=tenant, user_id=user, operation=operation,
                            key=key, request_hash=digest, response_jsonb=result))
    db.flush()
    return result


def audit(db, tenant, user, action, resource=None, request_id='', detail=None):
    db.add(AuditLog(tenant_id=tenant, user_id=user, action=action, resource_id=resource,
                    request_id=request_id, detail_jsonb=detail or {}))


def append_event(db, tenant, run_id, event_type, data):
    # Row lock serializes writers; messages, citations and completion can commit atomically.
    run = db.scalar(select(QueryRun).where(QueryRun.tenant_id == tenant,
                    QueryRun.id == run_id).with_for_update())
    if not run:
        raise PermissionError('FORBIDDEN_SCOPE')
    sequence = (db.scalar(select(func.max(RunEvent.sequence)).where(
        RunEvent.tenant_id == tenant, RunEvent.run_id == run_id)) or 0) + 1
    event = RunEvent(tenant_id=tenant, run_id=run_id, sequence=sequence,
                     event_type=event_type, data_jsonb=data)
    db.add(event)
    db.flush()
    return event


def enqueue_sync(db, tenant, version_id, target, action='upsert', payload=None):
    lock_key(db, f'outbox:{tenant}:{version_id}:{target}:{action}')
    item = db.scalar(select(SyncOutbox).where(SyncOutbox.tenant_id == tenant,
        SyncOutbox.aggregate_id == version_id, SyncOutbox.target == target, SyncOutbox.action == action))
    if not item:
        item = SyncOutbox(tenant_id=tenant, aggregate_type='version', aggregate_id=version_id,
                          target=target, action=action, payload_jsonb=payload or {})
        db.add(item)
        db.flush()
    return item
