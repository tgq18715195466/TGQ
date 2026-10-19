"""Business metadata never eager-loads file bytes. PostgreSQL is authoritative."""
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (JSON, Boolean, DateTime, Float, ForeignKeyConstraint, Index, Integer,
                        LargeBinary, String, Text, UniqueConstraint, Uuid, text)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, declared_attr


def now():
    return datetime.now(timezone.utc)


def uid():
    return str(uuid4())


J = JSON().with_variant(JSONB(), 'postgresql')
STRINGS = JSON().with_variant(ARRAY(Text()), 'postgresql')
UUIDS = JSON().with_variant(ARRAY(Uuid(as_uuid=False)), 'postgresql')


class Base(DeclarativeBase):
    pass


class Record:
    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    @declared_attr.directive
    def __table_args__(cls):
        return (UniqueConstraint('tenant_id', 'id'),)


class Tenant(Base):
    __tablename__ = 'tenants'
    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(30), default='ACTIVE')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class User(Record, Base):
    __tablename__ = 'users'
    external_subject: Mapped[str] = mapped_column(String(200), default=uid)
    name: Mapped[str] = mapped_column(String(200), default='')
    status: Mapped[str] = mapped_column(String(30), default='ACTIVE')


class LoginCredential(Record, Base):
    __tablename__ = 'login_credentials'
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    subject_tenant_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Role(Record, Base):
    __tablename__ = 'roles'
    name: Mapped[str] = mapped_column(String(50))


class UserRole(Record, Base):
    __tablename__ = 'user_roles'
    user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    role_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))


class KnowledgeBase(Record, Base):
    __tablename__ = 'knowledge_bases'
    name: Mapped[str] = mapped_column(String(200))
    created_by: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))


class KBMember(Record, Base):
    __tablename__ = 'kb_members'
    kb_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    role: Mapped[str] = mapped_column(String(30), default='reader')


class Document(Record, Base):
    __tablename__ = 'documents'
    kb_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    name: Mapped[str] = mapped_column(String(500))
    source_name: Mapped[str] = mapped_column(String(500), default='')
    mime_type: Mapped[str] = mapped_column(String(200), default='')
    sha256: Mapped[str] = mapped_column(String(64), default='')
    status: Mapped[str] = mapped_column(String(30), default='QUEUED')
    current_version_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    acl_jsonb: Mapped[dict] = mapped_column(J, default=dict)
    created_by: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DocumentVersion(Record, Base):
    __tablename__ = 'document_versions'
    document_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    version_no: Mapped[int] = mapped_column(Integer)
    original_blob_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    normalized_pdf_blob_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    parser_name: Mapped[str] = mapped_column(String(50), default='MinerU')
    parser_version: Mapped[str] = mapped_column(String(100), default='')
    parse_config_jsonb: Mapped[dict] = mapped_column(J, default=dict)
    chunk_config_version: Mapped[str] = mapped_column(String(100), default='chunk-v1')
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(30), default='QUEUED')
    quality_status: Mapped[str] = mapped_column(String(30), default='PENDING')
    raw_parse_jsonb: Mapped[dict] = mapped_column(J, default=dict, deferred=True)


class DocumentBlob(Record, Base):
    __tablename__ = 'document_blobs'
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    mime_type: Mapped[str] = mapped_column(String(200))
    size_bytes: Mapped[int] = mapped_column(Integer)
    blob_role: Mapped[str] = mapped_column(String(30))
    data: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)


class ParsedPage(Record, Base):
    __tablename__ = 'parsed_pages'
    version_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    page_no: Mapped[int] = mapped_column(Integer)
    width: Mapped[float | None] = mapped_column(Float)
    height: Mapped[float | None] = mapped_column(Float)
    metadata_jsonb: Mapped[dict] = mapped_column(J, default=dict)


class ParsedBlock(Record, Base):
    __tablename__ = 'parsed_blocks'
    version_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    page_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    block_no: Mapped[int] = mapped_column(Integer)
    block_type: Mapped[str] = mapped_column(String(30))
    text_content: Mapped[str] = mapped_column(Text, default='')
    markdown_content: Mapped[str] = mapped_column(Text, default='')
    table_html: Mapped[str | None] = mapped_column(Text)
    formula_latex: Mapped[str | None] = mapped_column(Text)
    bbox_jsonb: Mapped[list] = mapped_column(J, default=list)
    reading_order: Mapped[int] = mapped_column(Integer, default=0)
    heading_level: Mapped[int | None] = mapped_column(Integer)
    parent_block_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    asset_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    confidence: Mapped[float | None] = mapped_column(Float)
    metadata_jsonb: Mapped[dict] = mapped_column(J, default=dict)


class DocumentAsset(Record, Base):
    __tablename__ = 'document_assets'
    version_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    page_no: Mapped[int] = mapped_column(Integer)
    asset_type: Mapped[str] = mapped_column(String(30))
    mime_type: Mapped[str] = mapped_column(String(200))
    sha256: Mapped[str] = mapped_column(String(64))
    data: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)
    bbox_jsonb: Mapped[list] = mapped_column(J, default=list)


class Chunk(Record, Base):
    __tablename__ = 'chunks'
    kb_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    document_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    version_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    chunk_no: Mapped[int] = mapped_column(Integer)
    chunk_text: Mapped[str] = mapped_column(Text)
    embedding_text: Mapped[str] = mapped_column(Text)
    search_tokens: Mapped[str] = mapped_column(Text, default='')
    search_vector: Mapped[str | None] = mapped_column(Text().with_variant(TSVECTOR(), 'postgresql'))
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    content_type: Mapped[str] = mapped_column(String(30), default='text')
    section_path: Mapped[list] = mapped_column(STRINGS, default=list)
    page_start: Mapped[int] = mapped_column(Integer, default=1)
    page_end: Mapped[int] = mapped_column(Integer, default=1)
    block_ids: Mapped[list] = mapped_column(UUIDS, default=list)
    table_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    metadata_jsonb: Mapped[dict] = mapped_column(J, default=dict)
    embedding_status: Mapped[str] = mapped_column(String(30), default='PENDING')
    graph_status: Mapped[str] = mapped_column(String(30), default='PENDING')
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IngestJob(Record, Base):
    __tablename__ = 'ingest_jobs'
    version_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    current_stage: Mapped[str] = mapped_column(String(60), default='validate_file')
    status: Mapped[str] = mapped_column(String(30), default='QUEUED')
    progress: Mapped[float] = mapped_column(Float, default=0)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_detail: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    config_version: Mapped[str] = mapped_column(String(100), default='ingest-v1')


class JobStep(Record, Base):
    __tablename__ = 'job_steps'
    job_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    stage: Mapped[str] = mapped_column(String(60))
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(30), default='RUNNING')
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(80))
    metrics_jsonb: Mapped[dict] = mapped_column(J, default=dict)


class SyncOutbox(Record, Base):
    __tablename__ = 'sync_outbox'
    aggregate_type: Mapped[str] = mapped_column(String(30))
    aggregate_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    target: Mapped[str] = mapped_column(String(30))
    action: Mapped[str] = mapped_column(String(30))
    payload_jsonb: Mapped[dict] = mapped_column(J, default=dict)
    status: Mapped[str] = mapped_column(String(30), default='PENDING')
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(80))


class ChatSession(Record, Base):
    __tablename__ = 'chat_sessions'
    user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    title: Mapped[str] = mapped_column(String(200), default='新会话')


class Message(Record, Base):
    __tablename__ = 'messages'
    session_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    run_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    role: Mapped[str] = mapped_column(String(30))
    content: Mapped[str] = mapped_column(Text)
    answer_version: Mapped[int] = mapped_column(Integer, default=1)


class QueryRun(Record, Base):
    __tablename__ = 'query_runs'
    session_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    question: Mapped[str] = mapped_column(Text)
    kb_ids: Mapped[list] = mapped_column(UUIDS, default=list)
    route: Mapped[str] = mapped_column(String(40), default='fact')
    model_version: Mapped[str] = mapped_column(String(100), default='')
    prompt_version: Mapped[str] = mapped_column(String(100), default='evidence-v1')
    retrieval_config: Mapped[dict] = mapped_column(J, default=dict)
    timings: Mapped[dict] = mapped_column(J, default=dict)
    token_usage: Mapped[dict] = mapped_column(J, default=dict)
    status: Mapped[str] = mapped_column(String(30), default='QUEUED')
    error_code: Mapped[str | None] = mapped_column(String(80))
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    web_search_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class RetrievalCandidate(Record, Base):
    __tablename__ = 'retrieval_candidates'
    run_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    chunk_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    sources: Mapped[dict] = mapped_column(J, default=dict)
    rrf_score: Mapped[float] = mapped_column(Float, default=0)
    rerank_score: Mapped[float | None] = mapped_column(Float)
    selected: Mapped[bool] = mapped_column(Boolean, default=False)


class AnswerCitation(Record, Base):
    __tablename__ = 'answer_citations'
    run_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    answer_version: Mapped[int] = mapped_column(Integer, default=1)
    document_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    document_version: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    document_name: Mapped[str] = mapped_column(String(500))
    chunk_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    page_start: Mapped[int] = mapped_column(Integer)
    page_end: Mapped[int] = mapped_column(Integer)
    section_path: Mapped[list] = mapped_column(STRINGS, default=list)
    source_excerpt: Mapped[str] = mapped_column(Text)


class Feedback(Record, Base):
    __tablename__ = 'feedback'
    run_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    rating: Mapped[int] = mapped_column(Integer)
    comment: Mapped[str] = mapped_column(Text, default='')


class EvalDataset(Record, Base):
    __tablename__ = 'eval_datasets'
    name: Mapped[str] = mapped_column(String(200))
    version: Mapped[str] = mapped_column(String(100))
    sha256: Mapped[str] = mapped_column(String(64))


class EvalCase(Record, Base):
    __tablename__ = 'eval_cases'
    dataset_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    case_key: Mapped[str] = mapped_column(String(200))
    data_jsonb: Mapped[dict] = mapped_column(J)


class EvalRun(Record, Base):
    __tablename__ = 'eval_runs'
    dataset_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    status: Mapped[str] = mapped_column(String(30), default='QUEUED')
    config_jsonb: Mapped[dict] = mapped_column(J, default=dict)
    metrics_jsonb: Mapped[dict] = mapped_column(J, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(80))


class EvalResult(Record, Base):
    __tablename__ = 'eval_results'
    run_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    case_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    metrics_jsonb: Mapped[dict] = mapped_column(J, default=dict)
    prediction_jsonb: Mapped[dict] = mapped_column(J, default=dict)
    badcase: Mapped[str | None] = mapped_column(String(80))


class AuditLog(Record, Base):
    __tablename__ = 'audit_logs'
    user_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    action: Mapped[str] = mapped_column(String(100))
    resource_id: Mapped[str | None] = mapped_column(String(200))
    request_id: Mapped[str] = mapped_column(String(100), default='')
    detail_jsonb: Mapped[dict] = mapped_column(J, default=dict)


class RunEvent(Record, Base):
    __tablename__ = 'run_events'
    run_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    sequence: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(50))
    data_jsonb: Mapped[dict] = mapped_column(J, default=dict)


class IdempotencyRecord(Record, Base):
    __tablename__ = 'idempotency_records'
    user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    operation: Mapped[str] = mapped_column(String(200))
    key: Mapped[str] = mapped_column(String(200))
    request_hash: Mapped[str] = mapped_column(String(64))
    response_jsonb: Mapped[dict] = mapped_column(J)


class ReconcileIssue(Record, Base):
    __tablename__ = 'reconcile_issues'
    version_id: Mapped[str] = mapped_column(Uuid(as_uuid=False))
    detail_jsonb: Mapped[dict] = mapped_column(J, default=dict)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# Composite foreign keys prevent accidental cross-tenant references even for privileged workers.
def reference(child, column, parent):
    child.__table__.append_constraint(ForeignKeyConstraint(
        ['tenant_id', column], [f'{parent.__tablename__}.tenant_id', f'{parent.__tablename__}.id']))


for child, col, parent in [
    (UserRole, 'user_id', User), (UserRole, 'role_id', Role),
    (KBMember, 'kb_id', KnowledgeBase), (KBMember, 'user_id', User),
    (Document, 'kb_id', KnowledgeBase), (DocumentVersion, 'document_id', Document),
    (DocumentVersion, 'original_blob_id', DocumentBlob), (DocumentVersion, 'normalized_pdf_blob_id', DocumentBlob),
    (ParsedPage, 'version_id', DocumentVersion), (ParsedBlock, 'version_id', DocumentVersion),
    (DocumentAsset, 'version_id', DocumentVersion), (Chunk, 'version_id', DocumentVersion),
    (IngestJob, 'version_id', DocumentVersion), (JobStep, 'job_id', IngestJob),
    (ChatSession, 'user_id', User), (Message, 'session_id', ChatSession),
    (QueryRun, 'session_id', ChatSession), (RunEvent, 'run_id', QueryRun),
    (RetrievalCandidate, 'run_id', QueryRun), (AnswerCitation, 'run_id', QueryRun),
    (Feedback, 'run_id', QueryRun), (EvalCase, 'dataset_id', EvalDataset),
    (EvalRun, 'dataset_id', EvalDataset), (EvalResult, 'run_id', EvalRun),
]:
    reference(child, col, parent)

for cls, columns in [
    (User, ['external_subject']), (Role, ['name']), (UserRole, ['user_id', 'role_id']),
    (KBMember, ['kb_id', 'user_id']), (DocumentVersion, ['document_id', 'version_no']),
    (DocumentBlob, ['sha256', 'blob_role']), (ParsedPage, ['version_id', 'page_no']),
    (Chunk, ['document_id', 'version_id', 'chunk_no']), (JobStep, ['job_id', 'stage', 'attempt']),
    (SyncOutbox, ['aggregate_id', 'target', 'action']), (RunEvent, ['run_id', 'sequence']),
    (IdempotencyRecord, ['user_id', 'operation', 'key']),
    (EvalDataset, ['name', 'version']), (EvalCase, ['dataset_id', 'case_key']),
    (EvalResult, ['run_id', 'case_id']),
]:
    cls.__table__.append_constraint(UniqueConstraint('tenant_id', *columns))

Index('ix_chunks_scope_version', Chunk.tenant_id, Chunk.kb_id, Chunk.version_id)
Index('ix_chunks_search', Chunk.search_vector, postgresql_using='gin')
Index('ix_chunks_live', Chunk.tenant_id, Chunk.document_id, Chunk.version_id,
      postgresql_where=text('deleted_at IS NULL'))
Index('ix_documents_current', Document.tenant_id, Document.kb_id, Document.current_version_id,
      postgresql_where=text('deleted_at IS NULL'))
Index('ix_outbox_pending', SyncOutbox.status, SyncOutbox.available_at)
Index('ix_ingest_pending', IngestJob.status, IngestJob.available_at)
