import asyncio
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import jwt
from fastapi import Depends, FastAPI, File, Header, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text

from app.config import settings
from app.db import engine, transaction
from app.ingestion import FileLimits, validate_file
from app.models import (AnswerCitation, ChatSession, Chunk, Document, DocumentBlob, DocumentVersion, EvalDataset, EvalRun,
                        Feedback, IngestJob, KBMember, KnowledgeBase, LoginCredential, Message, ParsedBlock, ParsedPage,
                        QueryRun, RunEvent, Tenant, now, uid)
from app.password_auth import AUTH_TENANT_ID, LoginRateLimiter, hash_password, normalize_username, verify_password
from app.repository import (append_event, audit, document_allowed, enqueue_sync, idempotent, lock_key,
                            require_document, require_kb, save_blob, scoped)
from app.security import Identity, require_active_user, verify_token

logger = logging.getLogger(__name__)


class KBInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class LoginInput(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)


class SessionInput(BaseModel):
    title: str = Field(default='新会话', max_length=200)


class RunInput(BaseModel):
    session_id: UUID
    question: str = Field(min_length=1, max_length=8000)
    kb_ids: list[UUID] = Field(min_length=1, max_length=50)
    web_search_enabled: bool = False


class RetryInput(BaseModel):
    stage: str = 'parse_with_mineru'


class FeedbackInput(BaseModel):
    rating: int = Field(ge=-1, le=1)
    comment: str = Field(default='', max_length=8000)


class EvalInput(BaseModel):
    dataset_id: UUID
    kb_ids: list[UUID] = Field(min_length=1, max_length=50)
    use_ragas: bool = False


def plain(obj, exclude=()):
    result = {}
    for col in obj.__table__.columns:
        if col.name in {'data', 'raw_parse_jsonb', *exclude}:
            continue
        value = getattr(obj, col.name)
        if hasattr(value, 'isoformat'):
            value = value.isoformat()
        result[col.name] = value
    return result


def create_app(config=None, bind=None):
    config = config or settings()
    app = FastAPI(title='掌柜智库', version='0.1.0')
    app.state.config, app.state.bind = config, bind
    dummy_password_hash = hash_password('unused-password-123', salt=bytes(32))
    login_limiter = LoginRateLimiter()

    def tx(tenant):
        return transaction(tenant, bind=bind)

    @app.middleware('http')
    async def request_context(request, call_next):
        request.state.request_id = str(uuid4())
        response = await call_next(request)
        response.headers['X-Request-ID'] = request.state.request_id
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    def error(request, code, status=400, retryable=False):
        messages = {'AUTH_REQUIRED': '请提供有效凭证。', 'FORBIDDEN_SCOPE': '无权访问所请求资源。',
                    'INSUFFICIENT_EVIDENCE': '证据不足，请补充问题范围。',
                    'RATE_LIMITED': '请求过于频繁，请稍后重试。',
                    'INTERNAL_ERROR': '服务暂时无法处理请求。'}
        return JSONResponse(status_code=status, content={
            'request_id': getattr(request.state, 'request_id', str(uuid4())), 'error_code': code,
            'message': messages.get(code, '请求未满足处理条件。'), 'retryable': retryable})

    @app.exception_handler(PermissionError)
    async def forbidden(request, exc):
        code = str(exc) if str(exc) in {'AUTH_REQUIRED', 'FORBIDDEN_SCOPE'} else 'FORBIDDEN_SCOPE'
        return error(request, code, 401 if code == 'AUTH_REQUIRED' else 403)

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        code = getattr(exc, 'error_code', str(exc))
        safe_codes = {'IDEMPOTENCY_KEY_REQUIRED', 'IDEMPOTENCY_CONFLICT', 'FILE_TYPE_UNSUPPORTED',
                      'FILE_TOO_LARGE', 'PDF_PASSWORD_REQUIRED', 'FILE_CORRUPTED', 'PAGE_LIMIT_EXCEEDED',
                      'NORMALIZE_FAILED', 'INVALID_STAGE', 'INVALID_STATE', 'INVALID_EVENT_ID',
                      'WEB_SEARCH_DISABLED', 'DATASET_NOT_FOUND', 'RATE_LIMITED'}
        return error(request, code if code in safe_codes else 'INVALID_REQUEST',
                     429 if code == 'RATE_LIMITED' else 409 if code == 'IDEMPOTENCY_CONFLICT' else 400,
                     code == 'RATE_LIMITED' or getattr(exc, 'retryable', False))

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return error(request, 'NOT_FOUND' if exc.status_code == 404 else 'INVALID_REQUEST', exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation(request, exc):
        return error(request, 'INVALID_REQUEST', 422)

    @app.exception_handler(Exception)
    async def unexpected(request, exc):
        # Do not log SQL parameters or connection URLs containing secrets.
        logger.error('request_failed request_id=%s exception=%s', request.state.request_id, type(exc).__name__)
        return error(request, 'INTERNAL_ERROR', 500, True)

    def identity(request: Request, authorization: str | None = Header(default=None)):
        try:
            result = verify_token(authorization, config)
            with tx(result.tenant_id) as db:
                require_active_user(db, result)
            if config.redis_rate_limit_enabled:
                from app.temporary import rate_limit
                if not rate_limit(config.redis_url, result.tenant_id, result.user_id, config.rate_limit_per_minute):
                    raise ValueError('RATE_LIMITED')
            return result
        except PermissionError:
            with tx('00000000-0000-0000-0000-000000000000') as db:
                audit(db, '00000000-0000-0000-0000-000000000000', None, 'login.failed',
                      request_id=request.state.request_id)
            raise

    def own_run(db, who, run_id, lock=False):
        stmt = select(QueryRun).where(QueryRun.tenant_id == who.tenant_id, QueryRun.id == str(run_id))
        run = db.scalar(stmt.with_for_update().execution_options(populate_existing=True) if lock else stmt)
        if not run or run.user_id != who.user_id:
            raise PermissionError('FORBIDDEN_SCOPE')
        for kb in run.kb_ids:
            require_kb(db, who.tenant_id, who.user_id, kb)
        return run

    def idem(db, who, operation, key, body, action):
        payload = body if isinstance(body, bytes) else json.dumps(body, sort_keys=True, ensure_ascii=False).encode()
        return idempotent(db, who.tenant_id, who.user_id, operation, key, payload, action)

    web_dir = Path(__file__).resolve().parents[1] / 'web' / 'dist'
    if web_dir.is_dir():
        app.mount('/assets', StaticFiles(directory=web_dir / 'assets'), name='assets')

        @app.get('/', include_in_schema=False)
        def workspace():
            return FileResponse(web_dir / 'index.html', headers={'Cache-Control': 'no-cache'})

    @app.post('/api/v1/auth/login')
    def login(body: LoginInput, request: Request, response: Response):
        if len(config.jwt_secret) < 32:
            raise RuntimeError('AUTH_NOT_CONFIGURED')
        source = request.client.host if request.client else 'unknown'
        if not login_limiter.allow(source):
            raise ValueError('RATE_LIMITED')
        if config.redis_rate_limit_enabled:
            from app.temporary import rate_limit
            if not rate_limit(config.redis_url, 'login', source, 12):
                raise ValueError('RATE_LIMITED')
        try:
            username = normalize_username(body.username)
        except ValueError:
            username = ''
        subject = None
        with tx(AUTH_TENANT_ID) as db:
            credential = db.scalar(select(LoginCredential).where(
                LoginCredential.tenant_id == AUTH_TENANT_ID,
                LoginCredential.username == username).with_for_update())
            valid = verify_password(body.password, credential.password_hash if credential else dummy_password_hash)
            current = datetime.now(timezone.utc)
            lock_time = credential.locked_until if credential else None
            if lock_time and (lock_time if lock_time.tzinfo else lock_time.replace(tzinfo=timezone.utc)) <= current:
                credential.failed_attempts = 0
                credential.locked_until = None
                lock_time = None
            locked = bool(lock_time and (lock_time if lock_time.tzinfo else lock_time.replace(tzinfo=timezone.utc)) > current)
            if credential and valid:
                credential.failed_attempts = 0
                credential.locked_until = None
                subject = (credential.subject_tenant_id, credential.user_id)
            else:
                if credential and not locked:
                    credential.failed_attempts += 1
                    if credential.failed_attempts >= 5:
                        credential.locked_until = current + timedelta(minutes=15)
                audit(db, AUTH_TENANT_ID, None, 'login.failed', request_id=request.state.request_id)
        if not subject:
            raise PermissionError('AUTH_REQUIRED')
        tenant_id, user_id = subject
        with tx(tenant_id) as db:
            tenant = db.scalar(select(Tenant).where(Tenant.id == tenant_id, Tenant.status == 'ACTIVE'))
            if not tenant:
                raise PermissionError('AUTH_REQUIRED')
            require_active_user(db, Identity(tenant_id, user_id))
            audit(db, tenant_id, user_id, 'login.success', request_id=request.state.request_id)
        issued = int(time.time())
        token = jwt.encode({'tenant_id': tenant_id, 'sub': user_id, 'iss': config.jwt_issuer,
                            'aud': config.jwt_audience, 'iat': issued, 'exp': issued + 3600,
                            'amr': 'pwd'}, config.jwt_secret, algorithm='HS256')
        response.headers['Cache-Control'] = 'no-store'
        return {'access_token': token, 'token_type': 'bearer', 'expires_in': 3600}

    @app.get('/api/v1/me')
    def me(who: Identity = Depends(identity)):
        with tx(who.tenant_id) as db:
            user = require_active_user(db, who)
            return {'id': user.id, 'name': user.name, 'tenant_id': who.tenant_id}

    @app.get('/api/v1/system/status')
    def system_status(who: Identity = Depends(identity)):
        with tx(who.tenant_id) as db:
            db.execute(text('SELECT 1'))
        return {'api': 'ok', 'postgres': 'connected', 'environment': config.environment,
                'scanner_configured': bool(config.scanner_url), 'external_services_verified': False,
                'web_search_enabled': config.web_search_enabled,
                'worker_note': '外部解析、向量、图谱与模型服务尚需部署并验收；任务入队不代表处理完成。'}

    @app.get('/api/v1/kbs/{kb_id}/documents')
    def list_documents(kb_id: UUID, offset: int = Query(default=0, ge=0),
                       limit: int = Query(default=100, ge=1, le=100), who: Identity = Depends(identity)):
        with tx(who.tenant_id) as db:
            require_kb(db, who.tenant_id, who.user_id, str(kb_id))
            if db.get_bind().dialect.name == 'postgresql':
                acl_filter = text("(coalesce(jsonb_array_length(nullif(documents.acl_jsonb->'user_ids', 'null'::jsonb)), 0) = 0 "
                                  "OR (documents.acl_jsonb->'user_ids') @> cast(:acl_user as jsonb))")
                acl_user = json.dumps([who.user_id])
            else:
                acl_filter = text("(coalesce(json_array_length(documents.acl_jsonb, '$.user_ids'), 0) = 0 "
                                  "OR EXISTS (SELECT 1 FROM json_each(documents.acl_jsonb, '$.user_ids') "
                                  "WHERE value = :acl_user))")
                acl_user = who.user_id
            docs = db.scalars(select(Document).where(Document.tenant_id == who.tenant_id,
                Document.kb_id == str(kb_id), Document.deleted_at.is_(None), acl_filter)
                .params(acl_user=acl_user).order_by(Document.created_at.desc(), Document.id)
                .offset(offset).limit(limit)).all()
            if not docs:
                return []
            latest = select(DocumentVersion.document_id, func.max(DocumentVersion.version_no).label('number')).where(
                DocumentVersion.tenant_id == who.tenant_id, DocumentVersion.document_id.in_([d.id for d in docs])
                ).group_by(DocumentVersion.document_id).subquery()
            versions = {v.document_id: v for v in db.scalars(select(DocumentVersion).join(latest,
                (DocumentVersion.document_id == latest.c.document_id) & (DocumentVersion.version_no == latest.c.number))
                .where(DocumentVersion.tenant_id == who.tenant_id))}
            jobs = {}
            for job in db.scalars(select(IngestJob).where(IngestJob.tenant_id == who.tenant_id,
                IngestJob.version_id.in_([v.id for v in versions.values()])).order_by(IngestJob.created_at.desc())):
                jobs.setdefault(job.version_id, job)
            result = []
            for doc in docs:
                if not document_allowed(doc, who.user_id):
                    continue
                version = versions.get(doc.id)
                job = jobs.get(version.id) if version else None
                result.append({**plain(doc), 'latest_version': plain(version) if version else None,
                               'job': plain(job, exclude=('error_detail',)) if job else None})
            return result

    @app.get('/api/v1/chat/sessions')
    def list_sessions(who: Identity = Depends(identity)):
        with tx(who.tenant_id) as db:
            return [plain(row) for row in db.scalars(select(ChatSession).where(
                ChatSession.tenant_id == who.tenant_id, ChatSession.user_id == who.user_id)
                .order_by(ChatSession.created_at.desc()))]

    @app.get('/api/v1/chat/sessions/{session_id}/messages')
    def session_history(session_id: UUID, who: Identity = Depends(identity)):
        with tx(who.tenant_id) as db:
            session = scoped(db, ChatSession, who.tenant_id, str(session_id))
            if not session or session.user_id != who.user_id:
                raise PermissionError('FORBIDDEN_SCOPE')
            runs = db.scalars(select(QueryRun).where(QueryRun.tenant_id == who.tenant_id,
                QueryRun.session_id == session.id).order_by(QueryRun.created_at)).all()
            for run in runs:
                own_run(db, who, run.id)
            citations = db.scalars(select(AnswerCitation).where(AnswerCitation.tenant_id == who.tenant_id,
                AnswerCitation.run_id.in_([run.id for run in runs])).order_by(AnswerCitation.created_at)).all() if runs else []
            for citation in citations:
                require_document(db, who.tenant_id, who.user_id, citation.document_id)
            messages = db.scalars(select(Message).where(Message.tenant_id == who.tenant_id,
                Message.session_id == session.id).order_by(Message.created_at, Message.id)).all()
            return {'messages': [plain(row) for row in messages], 'runs': [plain(row) for row in runs],
                    'citations': [plain(row) for row in citations]}

    @app.get('/api/v1/eval-datasets')
    def list_eval_datasets(who: Identity = Depends(identity)):
        with tx(who.tenant_id) as db:
            return [plain(row) for row in db.scalars(select(EvalDataset).where(
                EvalDataset.tenant_id == who.tenant_id).order_by(EvalDataset.created_at.desc()))]

    @app.get('/api/v1/eval-runs')
    def list_eval_runs(who: Identity = Depends(identity)):
        with tx(who.tenant_id) as db:
            rows = db.scalars(select(EvalRun).where(EvalRun.tenant_id == who.tenant_id,
                EvalRun.user_id == who.user_id).order_by(EvalRun.created_at.desc())).all()
            result = []
            for row in rows:
                try:
                    for kb in row.config_jsonb.get('kb_ids', []):
                        require_kb(db, who.tenant_id, who.user_id, kb, admin=True)
                except PermissionError:
                    continue
                result.append(plain(row))
            return result

    @app.get('/health/live')
    def live():
        return {'status': 'ok'}

    @app.get('/health/ready')
    def ready():
        try:
            with (bind or engine()).connect() as conn:
                conn.execute(text('SELECT 1'))
            return {'status': 'ok', 'postgres': 'connected'}
        except Exception:
            return JSONResponse(status_code=503, content={'status': 'unavailable', 'postgres': 'unavailable'})

    @app.post('/api/v1/knowledge-bases', status_code=201)
    def create_kb(body: KBInput, request: Request, who: Identity = Depends(identity)):
        with tx(who.tenant_id) as db:
            kb = KnowledgeBase(tenant_id=who.tenant_id, name=body.name, created_by=who.user_id)
            db.add(kb); db.flush()
            db.add(KBMember(tenant_id=who.tenant_id, kb_id=kb.id, user_id=who.user_id, role='owner'))
            audit(db, who.tenant_id, who.user_id, 'kb.created', kb.id, request.state.request_id)
            return plain(kb)

    @app.get('/api/v1/knowledge-bases')
    def list_kbs(who: Identity = Depends(identity)):
        with tx(who.tenant_id) as db:
            rows = db.scalars(select(KnowledgeBase).join(KBMember, KBMember.kb_id == KnowledgeBase.id).where(
                KnowledgeBase.tenant_id == who.tenant_id, KBMember.tenant_id == who.tenant_id,
                KBMember.user_id == who.user_id)).all()
            return [plain(x) for x in rows]

    @app.post('/api/v1/kbs/{kb_id}/documents', status_code=202)
    async def upload(kb_id: UUID, request: Request, file: UploadFile = File(),
                     document_id: UUID | None = Query(default=None), who: Identity = Depends(identity),
                     key: str | None = Header(default=None, alias='Idempotency-Key')):
        tenant, kb = who.tenant_id, str(kb_id)
        with tx(tenant) as db:
            require_kb(db, tenant, who.user_id, kb, write=True)
        data = await file.read(config.max_file_size_mb * 1024 * 1024 + 1)
        verified = validate_file(data, file.filename or '', file.content_type or '',
                                 FileLimits(max_bytes=config.max_file_size_mb * 1024 * 1024,
                                            max_pages=config.max_document_pages))
        with tx(tenant) as db:
            require_kb(db, tenant, who.user_id, kb, write=True)
            def action():
                lock_key(db, f'upload:{tenant}:{kb}:{document_id or verified.sha256}')
                if document_id:
                    doc = require_document(db, tenant, who.user_id, str(document_id), write=True)
                    if doc.kb_id != kb:
                        raise PermissionError('FORBIDDEN_SCOPE')
                    same = db.scalar(select(DocumentVersion).join(DocumentBlob,
                        DocumentVersion.original_blob_id == DocumentBlob.id).where(
                        DocumentVersion.tenant_id == tenant, DocumentVersion.document_id == doc.id,
                        DocumentBlob.tenant_id == tenant, DocumentBlob.sha256 == verified.sha256))
                else:
                    doc = db.scalar(select(Document).where(Document.tenant_id == tenant, Document.kb_id == kb,
                        Document.sha256 == verified.sha256, Document.deleted_at.is_(None)))
                    if doc and not document_allowed(doc, who.user_id):
                        raise PermissionError('FORBIDDEN_SCOPE')
                    same = db.scalar(select(DocumentVersion).where(DocumentVersion.tenant_id == tenant,
                        DocumentVersion.document_id == doc.id).order_by(DocumentVersion.version_no.desc())) if doc else None
                if same:
                    job = db.scalar(select(IngestJob).where(IngestJob.tenant_id == tenant,
                                                           IngestJob.version_id == same.id))
                    return {'document_id': doc.id, 'version_id': same.id, 'job_id': job.id if job else None,
                            'status': same.status, 'deduplicated': True}
                blob = save_blob(db, tenant, data, verified.mime_type, 'original')
                if doc is None:
                    doc = Document(tenant_id=tenant, kb_id=kb, name=verified.filename, source_name=verified.filename,
                        mime_type=verified.mime_type, sha256=verified.sha256, created_by=who.user_id)
                    db.add(doc); db.flush()
                number = (db.scalar(select(func.max(DocumentVersion.version_no)).where(
                    DocumentVersion.tenant_id == tenant, DocumentVersion.document_id == doc.id)) or 0) + 1
                version = DocumentVersion(tenant_id=tenant, document_id=doc.id, version_no=number,
                    original_blob_id=blob.id, parse_config_jsonb={'filename': verified.filename,
                    'mime_type': verified.mime_type}, chunk_config_version=config.chunk_config_version)
                db.add(version); db.flush()
                job = IngestJob(tenant_id=tenant, version_id=version.id)
                db.add(job); db.flush()
                audit(db, tenant, who.user_id, 'document.upload' if number == 1 else 'document.update',
                      doc.id, request.state.request_id)
                return {'document_id': doc.id, 'version_id': version.id, 'job_id': job.id,
                        'status': 'QUEUED', 'deduplicated': False}
            return idem(db, who, f'upload:{kb}', key,
                        f'{document_id}:{verified.filename}:{verified.mime_type}:{verified.sha256}'.encode(), action)

    @app.get('/api/v1/documents/{document_id}')
    def get_document(document_id: UUID, who: Identity = Depends(identity)):
        with tx(who.tenant_id) as db:
            doc = require_document(db, who.tenant_id, who.user_id, str(document_id))
            versions = db.scalars(select(DocumentVersion).where(DocumentVersion.tenant_id == who.tenant_id,
                                   DocumentVersion.document_id == doc.id).order_by(DocumentVersion.version_no)).all()
            return {'document': plain(doc), 'versions': [plain(v) for v in versions]}

    @app.get('/api/v1/documents/{document_id}/content')
    def content(document_id: UUID, kind: str = 'chunks', offset: int = Query(default=0, ge=0),
                limit: int = Query(default=100, ge=1, le=1000), who: Identity = Depends(identity)):
        with tx(who.tenant_id) as db:
            doc = require_document(db, who.tenant_id, who.user_id, str(document_id))
            model = {'chunks': Chunk, 'pages': ParsedPage, 'blocks': ParsedBlock}.get(kind)
            if not model:
                raise ValueError('INVALID_REQUEST')
            if not doc.current_version_id:
                return []
            order = {'chunks': Chunk.chunk_no, 'pages': ParsedPage.page_no, 'blocks': ParsedBlock.reading_order}[kind]
            rows = db.scalars(select(model).where(model.tenant_id == who.tenant_id,
                              model.version_id == doc.current_version_id).order_by(order).offset(offset).limit(limit)).all()
            return [plain(x) for x in rows]

    @app.get('/api/v1/documents/{document_id}/download')
    def download(document_id: UUID, request: Request, who: Identity = Depends(identity)):
        with tx(who.tenant_id) as db:
            doc = require_document(db, who.tenant_id, who.user_id, str(document_id))
            version = db.scalar(select(DocumentVersion).where(DocumentVersion.tenant_id == who.tenant_id,
                DocumentVersion.document_id == doc.id).order_by(DocumentVersion.version_no.desc()))
            blob = scoped(db, DocumentBlob, who.tenant_id, version.original_blob_id)
            audit(db, who.tenant_id, who.user_id, 'document.download', doc.id, request.state.request_id)
            return Response(blob.data, media_type=blob.mime_type,
                            headers={'Content-Disposition': 'attachment', 'Cache-Control': 'no-store'})

    @app.get('/api/v1/jobs/{job_id}')
    def get_job(job_id: UUID, who: Identity = Depends(identity)):
        with tx(who.tenant_id) as db:
            job = scoped(db, IngestJob, who.tenant_id, str(job_id))
            if not job:
                raise PermissionError('FORBIDDEN_SCOPE')
            version = scoped(db, DocumentVersion, who.tenant_id, job.version_id)
            require_document(db, who.tenant_id, who.user_id, version.document_id)
            return plain(job, exclude=('error_detail',))

    @app.post('/api/v1/document-versions/{version_id}/retry', status_code=202)
    def retry(version_id: UUID, body: RetryInput, request: Request, who: Identity = Depends(identity),
              key: str | None = Header(default=None, alias='Idempotency-Key')):
        from app.workflows import INGEST_STAGES
        if body.stage not in INGEST_STAGES:
            raise ValueError('INVALID_STAGE')
        with tx(who.tenant_id) as db:
            version = scoped(db, DocumentVersion, who.tenant_id, str(version_id))
            if not version:
                raise PermissionError('FORBIDDEN_SCOPE')
            doc = require_document(db, who.tenant_id, who.user_id, version.document_id, write=True)
            def action():
                if doc.current_version_id == version.id:
                    raise ValueError('INVALID_STATE')
                job = db.scalar(select(IngestJob).where(IngestJob.tenant_id == who.tenant_id,
                                IngestJob.version_id == version.id).with_for_update())
                if job.status not in {'FAILED', 'CANCELLED'}:
                    raise ValueError('INVALID_STATE')
                if INGEST_STAGES.index(body.stage) > INGEST_STAGES.index(job.current_stage):
                    raise ValueError('INVALID_STAGE')
                job.status, job.current_stage = 'RETRYING', body.stage
                job.cancel_requested, job.available_at, job.retry_count = False, now(), 0
                job.config_version = uid()  # fresh checkpoint namespace for explicit operator retry
                audit(db, who.tenant_id, who.user_id, 'document.reindex', doc.id, request.state.request_id)
                return {'job_id': job.id, 'status': job.status}
            return idem(db, who, f'retry:{version_id}', key, body.model_dump(), action)

    @app.post('/api/v1/jobs/{job_id}/cancel')
    def cancel_job(job_id: UUID, who: Identity = Depends(identity),
                   key: str | None = Header(default=None, alias='Idempotency-Key')):
        with tx(who.tenant_id) as db:
            job = scoped(db, IngestJob, who.tenant_id, str(job_id))
            if not job:
                raise PermissionError('FORBIDDEN_SCOPE')
            version = scoped(db, DocumentVersion, who.tenant_id, job.version_id)
            require_document(db, who.tenant_id, who.user_id, version.document_id, write=True)
            def action():
                if job.status != 'READY':
                    job.cancel_requested, job.status = True, 'CANCELLED'
                return {'job_id': job.id, 'status': job.status}
            return idem(db, who, f'cancel-job:{job_id}', key, {}, action)

    @app.delete('/api/v1/documents/{document_id}', status_code=202)
    def delete_document(document_id: UUID, request: Request, hard: bool = False,
                        who: Identity = Depends(identity),
                        key: str | None = Header(default=None, alias='Idempotency-Key')):
        with tx(who.tenant_id) as db:
            doc = require_document(db, who.tenant_id, who.user_id, str(document_id),
                                   write=True, include_deleted=True)
            if hard:
                require_kb(db, who.tenant_id, who.user_id, doc.kb_id, admin=True)
            def action():
                doc.deleted_at, doc.status = now(), 'DELETING'
                versions = db.scalars(select(DocumentVersion).where(DocumentVersion.tenant_id == who.tenant_id,
                                     DocumentVersion.document_id == doc.id)).all()
                for version in versions:
                    for target in ('milvus', 'neo4j'):
                        item = enqueue_sync(db, who.tenant_id, version.id, target, 'delete',
                                            {'document_id': doc.id, 'hard': hard})
                        if hard and not item.payload_jsonb.get('hard'):
                            item.payload_jsonb = {**item.payload_jsonb, 'hard': True}
                            item.status = 'PENDING'
                    for job in db.scalars(select(IngestJob).where(IngestJob.tenant_id == who.tenant_id,
                                           IngestJob.version_id == version.id)):
                        job.cancel_requested = True
                audit(db, who.tenant_id, who.user_id, 'document.hard_delete' if hard else 'document.delete',
                      doc.id, request.state.request_id)
                return {'document_id': doc.id, 'status': 'DELETING', 'hard': hard}
            return idem(db, who, f'delete:{document_id}', key, {'hard': hard}, action)

    @app.post('/api/v1/chat/sessions', status_code=201)
    def session(body: SessionInput, who: Identity = Depends(identity)):
        with tx(who.tenant_id) as db:
            row = ChatSession(tenant_id=who.tenant_id, user_id=who.user_id, title=body.title)
            db.add(row); db.flush()
            return plain(row)

    @app.post('/api/v1/chat/runs', status_code=202)
    def run(body: RunInput, request: Request, who: Identity = Depends(identity)):
        with tx(who.tenant_id) as db:
            session = scoped(db, ChatSession, who.tenant_id, str(body.session_id))
            if not session or session.user_id != who.user_id:
                raise PermissionError('FORBIDDEN_SCOPE')
            for kb in body.kb_ids:
                require_kb(db, who.tenant_id, who.user_id, str(kb))
            if body.web_search_enabled and not config.web_search_enabled:
                raise ValueError('WEB_SEARCH_DISABLED')
            row = QueryRun(tenant_id=who.tenant_id, user_id=who.user_id, session_id=session.id,
                question=body.question, kb_ids=[str(kb) for kb in body.kb_ids],
                model_version=config.llm_model, prompt_version=config.prompt_version,
                retrieval_config=config.retrieval_snapshot(), web_search_requested=body.web_search_enabled)
            db.add(row); db.flush()
            db.add(Message(tenant_id=who.tenant_id, session_id=session.id, run_id=row.id,
                           role='user', content=body.question))
            append_event(db, who.tenant_id, row.id, 'run.started', {'run_id': row.id})
            if body.web_search_enabled:
                audit(db, who.tenant_id, who.user_id, 'search.external_enabled', row.id, request.state.request_id)
            return {'run_id': row.id, 'status': row.status}

    @app.get('/api/v1/chat/runs/{run_id}/events')
    def events(run_id: UUID, who: Identity = Depends(identity),
               last: str = Header(default='0', alias='Last-Event-ID')):
        if not last.isdecimal() or len(last) > 12:
            raise ValueError('INVALID_EVENT_ID')
        with tx(who.tenant_id) as db:
            own_run(db, who, run_id)
        async def stream():
            cursor = int(last)
            while True:
                with tx(who.tenant_id) as db:
                    run = own_run(db, who, run_id)
                    rows = db.scalars(select(RunEvent).where(RunEvent.tenant_id == who.tenant_id,
                        RunEvent.run_id == str(run_id), RunEvent.sequence > cursor).order_by(RunEvent.sequence)
                        .limit(200)).all()
                    status = run.status
                    payloads = [(row.sequence, row.event_type, row.data_jsonb) for row in rows]
                for seq, event, data in payloads:
                    cursor = seq
                    yield f'id: {seq}\nevent: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n'
                if status in {'COMPLETED', 'FAILED', 'CANCELLED'} and len(payloads) < 200:
                    break
                if not payloads:
                    yield ': heartbeat\n\n'
                    await asyncio.sleep(1)
        return StreamingResponse(stream(), media_type='text/event-stream',
                                 headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

    @app.post('/api/v1/chat/runs/{run_id}/cancel')
    def cancel_run(run_id: UUID, who: Identity = Depends(identity),
                   key: str | None = Header(default=None, alias='Idempotency-Key')):
        with tx(who.tenant_id) as db:
            run = own_run(db, who, run_id, lock=True)
            def action():
                if run.status not in {'COMPLETED', 'FAILED', 'CANCELLED'}:
                    run.cancel_requested, run.status = True, 'CANCELLED'
                    append_event(db, who.tenant_id, run.id, 'run.failed', {'error_code': 'CANCELLED'})
                return {'run_id': run.id, 'status': run.status}
            return idem(db, who, f'cancel-run:{run_id}', key, {}, action)

    @app.post('/api/v1/chat/runs/{run_id}/feedback')
    def feedback(run_id: UUID, body: FeedbackInput, who: Identity = Depends(identity),
                 key: str | None = Header(default=None, alias='Idempotency-Key')):
        with tx(who.tenant_id) as db:
            own_run(db, who, run_id)
            def action():
                row = Feedback(tenant_id=who.tenant_id, run_id=str(run_id), user_id=who.user_id,
                               **body.model_dump())
                db.add(row); db.flush()
                return {'id': row.id}
            return idem(db, who, f'feedback:{run_id}', key, body.model_dump(), action)

    @app.post('/api/v1/eval-runs', status_code=202)
    def eval_run(body: EvalInput, who: Identity = Depends(identity)):
        with tx(who.tenant_id) as db:
            for kb in body.kb_ids:
                require_kb(db, who.tenant_id, who.user_id, str(kb), admin=True)
            if not scoped(db, EvalDataset, who.tenant_id, str(body.dataset_id)):
                raise ValueError('DATASET_NOT_FOUND')
            row = EvalRun(tenant_id=who.tenant_id, dataset_id=str(body.dataset_id), user_id=who.user_id,
                          config_jsonb={**config.retrieval_snapshot(), **body.model_dump(mode='json')})
            db.add(row); db.flush()
            return plain(row)

    @app.get('/api/v1/eval-runs/{run_id}')
    def get_eval(run_id: UUID, request: Request, who: Identity = Depends(identity)):
        with tx(who.tenant_id) as db:
            row = scoped(db, EvalRun, who.tenant_id, str(run_id))
            if not row or row.user_id != who.user_id:
                raise PermissionError('FORBIDDEN_SCOPE')
            for kb in row.config_jsonb.get('kb_ids', []):
                require_kb(db, who.tenant_id, who.user_id, kb, admin=True)
            audit(db, who.tenant_id, who.user_id, 'eval.export', row.id, request.state.request_id)
            return plain(row)

    return app


app = create_app()
