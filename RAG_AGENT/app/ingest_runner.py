"""Durable, retryable ingestion stage implementations (long work runs only in Worker)."""
import asyncio
import base64
from dataclasses import asdict
import hashlib
import json
import time
from pathlib import PurePosixPath
from uuid import NAMESPACE_URL, uuid5

import jieba
from sqlalchemy import delete, func, select

from app.db import transaction
from app.ingestion import (ChunkConfig, DevelopmentBypassScanner, FileLimits, HTTPMalwareScanner,
                           IngestionError, MinerUClient, NormalizeConfig, ParseResult,
                           ParsedBlock as Block, build_outline, create_chunks, normalize_file,
                           scan_file, validate_file, validate_parse_quality)
from app.models import (Chunk, Document, DocumentAsset, DocumentBlob, DocumentVersion, IngestJob,
                        JobStep, ParsedBlock, ParsedPage, now)
from app.repository import enqueue_sync, publish_version, save_blob, scoped
from app.sync import deliver_outbox
from app.workflows import INGEST_STAGES, STAGE_STATUS


def tokens(text):
    return ' '.join(token for token in jieba.cut(text) if token.strip())


class IngestRunner:
    def __init__(self, config, services, bind=None):
        self.config, self.services, self.bind = config, services, bind
        self.embeddings, self.graph_data = None, None

    def tx(self, tenant):
        return transaction(tenant, bind=self.bind)

    def records(self, db, tenant, job_id):
        job = scoped(db, IngestJob, tenant, job_id)
        if not job:
            raise IngestionError('CANCELLED')
        version = scoped(db, DocumentVersion, tenant, job.version_id)
        doc = scoped(db, Document, tenant, version.document_id) if version else None
        if not doc or doc.deleted_at or job.cancel_requested:
            raise IngestionError('CANCELLED')
        return job, version, doc

    def __call__(self, stage, state):
        tenant, job_id = state['tenant_id'], state['job_id']
        started = time.monotonic()
        with self.tx(tenant) as db:
            job, version, doc = self.records(db, tenant, job_id)
            job.current_stage, job.status = stage, STAGE_STATUS[stage]
            job.progress = INGEST_STAGES.index(stage) / len(INGEST_STAGES)
            attempt = (db.scalar(select(func.max(JobStep.attempt)).where(JobStep.tenant_id == tenant,
                         JobStep.job_id == job_id, JobStep.stage == stage)) or 0) + 1
            step = JobStep(tenant_id=tenant, job_id=job_id, stage=stage, attempt=attempt)
            db.add(step); db.flush()
            step_id = step.id
        try:
            self.execute(stage, tenant, job_id)
        except Exception as exc:
            with self.tx(tenant) as db:
                step = scoped(db, JobStep, tenant, step_id)
                if step:
                    step.status, step.finished_at = 'FAILED', now()
                    step.error_code = getattr(exc, 'error_code', 'INTERNAL_ERROR')
            raise
        with self.tx(tenant) as db:
            job, version, doc = self.records(db, tenant, job_id)
            step = scoped(db, JobStep, tenant, step_id)
            step.status, step.finished_at = 'DONE', now()
            step.metrics_jsonb = {'duration_ms': round((time.monotonic() - started) * 1000),
                                  'config_version': job.config_version}
            if stage == INGEST_STAGES[-1]:
                job.status, job.progress = 'READY', 1.0

    def load_parse(self, db, tenant, version):
        rows = db.scalars(select(ParsedBlock).where(ParsedBlock.tenant_id == tenant,
                         ParsedBlock.version_id == version.id).order_by(ParsedBlock.reading_order)).all()
        page_map = dict(db.execute(select(ParsedPage.id, ParsedPage.page_no).where(
            ParsedPage.tenant_id == tenant, ParsedPage.version_id == version.id)).all())
        blocks = [Block(block_id=x.id, page_no=page_map[x.page_id], block_type=x.block_type,
            text=x.text_content, markdown=x.markdown_content, bbox=x.bbox_jsonb, reading_order=x.reading_order,
            heading_level=x.heading_level, parent_block_id=x.parent_block_id, table_html=x.table_html,
            formula_latex=x.formula_latex, asset_id=x.asset_id, confidence=x.confidence) for x in rows]
        return ParseResult(blocks, {}, len(page_map), version.parser_version)

    def execute(self, stage, tenant, job_id):
        if stage in {'validate_file', 'persist_document'}:
            with self.tx(tenant) as db:
                job, version, doc = self.records(db, tenant, job_id)
                blob = scoped(db, DocumentBlob, tenant, version.original_blob_id)
                if not blob:
                    raise IngestionError('FILE_CORRUPTED')
                data, mime, filename = blob.data, blob.mime_type, version.parse_config_jsonb['filename']
            if stage == 'persist_document':
                return  # upload transaction already stored SHA256-verified bytes and durable job.
            validate_file(data, filename, mime, FileLimits(max_bytes=self.config.max_file_size_mb * 1024 * 1024,
                                                         max_pages=self.config.max_document_pages))
            scanner = HTTPMalwareScanner(self.config.scanner_url) if self.config.scanner_url else None
            if self.config.development_skip_scanner and self.config.environment == 'development':
                scanner = DevelopmentBypassScanner()
            asyncio.run(scan_file(data, scanner))
            with self.tx(tenant) as db:
                job, version, doc = self.records(db, tenant, job_id)
                version.parse_config_jsonb = {**version.parse_config_jsonb, 'scan_attestation': {
                    'sha256': hashlib.sha256(data).hexdigest(), 'environment': self.config.environment,
                    'scanner_url': self.config.scanner_url,
                    'development_bypass': isinstance(scanner, DevelopmentBypassScanner)}}
        elif stage == 'normalize_file':
            with self.tx(tenant) as db:
                job, version, doc = self.records(db, tenant, job_id)
                blob = scoped(db, DocumentBlob, tenant, version.original_blob_id)
                data = blob.data
                attestation = version.parse_config_jsonb.get('scan_attestation', {})
                if attestation.get('sha256') != blob.sha256 or (
                    attestation.get('development_bypass') and self.config.environment != 'development'):
                    raise IngestionError('MALWARE_SCAN_REQUIRED')
                validated = validate_file(data, version.parse_config_jsonb['filename'], blob.mime_type,
                    FileLimits(max_bytes=self.config.max_file_size_mb * 1024 * 1024,
                               max_pages=self.config.max_document_pages))
            normalized = normalize_file(data, validated, NormalizeConfig(max_pages=self.config.max_document_pages))
            with self.tx(tenant) as db:
                job, version, doc = self.records(db, tenant, job_id)
                blob = save_blob(db, tenant, normalized.pdf, 'application/pdf', 'normalized')
                version.normalized_pdf_blob_id, version.page_count = blob.id, normalized.page_count
                version.parse_config_jsonb = {**version.parse_config_jsonb, 'normalization': {
                    key: value for key, value in asdict(normalized).items() if key != 'pdf'}}
        elif stage == 'parse_with_mineru':
            with self.tx(tenant) as db:
                job, version, doc = self.records(db, tenant, job_id)
                blob = scoped(db, DocumentBlob, tenant, version.normalized_pdf_blob_id)
                if not blob:
                    raise IngestionError('NORMALIZE_FAILED')
                data, version_id = blob.data, version.id
            parsed = asyncio.run(MinerUClient(self.config.mineru_base_url,
                                  self.config.mineru_timeout_seconds).parse(data, version_id))
            with self.tx(tenant) as db:
                job, version, doc = self.records(db, tenant, job_id)
                if doc.current_version_id == version.id:
                    raise IngestionError('INVALID_STATE')
                for model in [ParsedBlock, ParsedPage, DocumentAsset]:
                    db.execute(delete(model).where(model.tenant_id == tenant, model.version_id == version.id))
                version.raw_parse_jsonb, version.parser_version = parsed.raw, parsed.parser_version
                version.quality_status = 'PENDING'
                pages = {}
                parser_document = next(iter(parsed.raw['results'].values()))
                middle = parser_document.get('middle_json', {})
                middle = json.loads(middle) if isinstance(middle, str) else middle
                page_info = middle.get('pdf_info', [])
                for page_no in range(1, parsed.page_count + 1):
                    info = page_info[page_no - 1] if page_no <= len(page_info) else {}
                    size = info.get('page_size', [None, None])
                    page = ParsedPage(tenant_id=tenant, version_id=version.id, page_no=page_no,
                                      width=size[0], height=size[1], metadata_jsonb={'parser_page_idx': page_no-1})
                    db.add(page); db.flush()
                    pages[page_no] = page.id
                for index, block in enumerate(parsed.blocks):
                    db.add(ParsedBlock(id=block.block_id, tenant_id=tenant, version_id=version.id,
                        page_id=pages[block.page_no], block_no=index, block_type=block.block_type,
                        text_content=block.text, markdown_content=block.markdown, table_html=block.table_html,
                        formula_latex=block.formula_latex, bbox_jsonb=block.bbox, reading_order=block.reading_order,
                        heading_level=block.heading_level, parent_block_id=block.parent_block_id,
                        asset_id=block.asset_id, confidence=block.confidence))
                self.persist_assets(db, tenant, version.id, parsed)
        elif stage == 'validate_parse_quality':
            with self.tx(tenant) as db:
                job, version, doc = self.records(db, tenant, job_id)
                report = validate_parse_quality(self.load_parse(db, tenant, version), version.page_count)
                version.quality_status = report.status
                version.parse_config_jsonb = {**version.parse_config_jsonb, 'quality': asdict(report)}
            if report.status == 'BLOCK':
                raise IngestionError('PARSE_QUALITY_BLOCKED')
        elif stage == 'build_outline':
            with self.tx(tenant) as db:
                job, version, doc = self.records(db, tenant, job_id)
                outline = build_outline(self.load_parse(db, tenant, version).blocks)
                version.parse_config_jsonb = {**version.parse_config_jsonb, 'outline': asdict(outline)}
        elif stage == 'create_chunks':
            with self.tx(tenant) as db:
                job, version, doc = self.records(db, tenant, job_id)
                if version.quality_status not in {'PASS', 'WARN'}:
                    raise IngestionError('PARSE_QUALITY_BLOCKED')
                chunks = create_chunks(self.load_parse(db, tenant, version).blocks,
                    doc.id, version.id, doc.name, ChunkConfig(chunk_config_version=version.chunk_config_version))
                if not chunks:
                    raise IngestionError('PARSE_QUALITY_BLOCKED')
                db.execute(delete(Chunk).where(Chunk.tenant_id == tenant, Chunk.version_id == version.id))
                for chunk in chunks:
                    db.add(Chunk(id=chunk.chunk_id, tenant_id=tenant, kb_id=doc.kb_id, document_id=doc.id,
                        version_id=version.id, chunk_no=chunk.chunk_sequence, chunk_text=chunk.text,
                        embedding_text=chunk.embedding_text, search_tokens=tokens(chunk.text),
                        token_count=chunk.token_count, content_type=chunk.block_type, section_path=chunk.section_path,
                        page_start=chunk.page_start, page_end=chunk.page_end, block_ids=chunk.block_ids,
                        table_id=chunk.table_id, metadata_jsonb={'row_start': chunk.row_start,
                        'row_end': chunk.row_end, 'table_html': chunk.table_html, 'markdown': chunk.markdown}))
                for target in ('milvus', 'neo4j'):
                    item = enqueue_sync(db, tenant, version.id, target)
                    item.status, item.attempts = 'PENDING', 0
                    item.payload_jsonb = {'repair': True}  # replace old IDs if parse/config changed
        elif stage in {'generate_embeddings', 'extract_entities'}:
            with self.tx(tenant) as db:
                job, version, doc = self.records(db, tenant, job_id)
                chunks = db.scalars(select(Chunk).where(Chunk.tenant_id == tenant,
                                    Chunk.version_id == version.id).order_by(Chunk.chunk_no)).all()
            if stage == 'generate_embeddings':
                self.embeddings = self.services.embed([x.embedding_text for x in chunks])
            else:
                self.graph_data = self.services.extract_graph(chunks)
        elif stage in {'sync_milvus', 'sync_neo4j'}:
            with self.tx(tenant) as db:
                job, version, doc = self.records(db, tenant, job_id)
                chunks = db.scalars(select(Chunk).where(Chunk.tenant_id == tenant,
                                   Chunk.version_id == version.id).order_by(Chunk.chunk_no)).all()
                target = 'milvus' if stage == 'sync_milvus' else 'neo4j'
                item = enqueue_sync(db, tenant, version.id, target)
                def deliver(row):
                    if row.payload_jsonb.get('repair'):
                        method = self.services.delete_vectors if target == 'milvus' else self.services.delete_graph
                        method(tenant, version.id, row.id)
                    if target == 'milvus':
                        self.services.sync_vectors(tenant, version.id, chunks, [doc], row.id, embeddings=self.embeddings)
                    else:
                        self.services.sync_graph(tenant, version.id, chunks, [doc], row.id, graph=self.graph_data)
                success = deliver_outbox(db, item, deliver, self.config.max_retries)
                if success:
                    for chunk in chunks:
                        if target == 'milvus':
                            chunk.embedding_status = 'DONE'
                        else:
                            chunk.graph_status = 'DONE'
                code = item.error_code
            if not success:
                raise IngestionError(code, retryable=True)
        elif stage == 'publish_version':
            with self.tx(tenant) as db:
                job, version, doc = self.records(db, tenant, job_id)
                publish_version(db, tenant, version.id)
        elif stage == 'cleanup_temp_files':
            self.embeddings, self.graph_data = None, None

    def persist_assets(self, db, tenant, version_id, parsed):
        result = next(iter(parsed.raw.get('results', {}).values()), {})
        images = result.get('images', {}) or {}
        if isinstance(images, str):
            images = json.loads(images)
        for name, encoded in images.items():
            asset_id = str(uuid5(NAMESPACE_URL, f'{version_id}:asset:{name}'))
            block = next((b for b in parsed.blocks if b.asset_id == asset_id), None)
            if block is None:
                content = result.get('content_list', [])
                content = json.loads(content) if isinstance(content, str) else content
                paths = [entry.get('img_path', '') for entry in content
                         if PurePosixPath(entry.get('img_path', '')).name == PurePosixPath(name).name]
                ids = {str(uuid5(NAMESPACE_URL, f'{version_id}:asset:{path}')) for path in paths}
                block = next((b for b in parsed.blocks if b.asset_id in ids), None)
            if not block:
                continue
            asset_id = block.asset_id
            mime = 'image/png'
            if encoded.startswith('data:'):
                header, encoded = encoded.split(',', 1)
                mime = header[5:].split(';')[0]
            data = base64.b64decode(encoded, validate=True)
            if len(data) > 20 * 1024 * 1024:
                raise IngestionError('FILE_TOO_LARGE')
            db.add(DocumentAsset(id=asset_id, tenant_id=tenant, version_id=version_id,
                page_no=block.page_no, asset_type='image', mime_type=mime,
                sha256=hashlib.sha256(data).hexdigest(), data=data, bbox_jsonb=block.bbox))
