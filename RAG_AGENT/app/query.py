"""Parallel hybrid recall, verified evidence and transactional durable SSE output."""
import json
import re
import time
from typing import TypedDict

import httpx
from langgraph.graph import StateGraph, START, END
from sqlalchemy import delete, select

from app.adapters import Services
from app.db import transaction
from app.keyword import bm25_rank
from app.models import (AnswerCitation, Document, Message, QueryRun, RetrievalCandidate)
from app.repository import (append_event, authorized_chunks, document_allowed, require_kb, scoped)
from app.retrieval import classify, evidence_gate, fuse, select_context, verify_claims, verify_summary_claims

REFUSAL = '现有授权资料不足以支持确定结论。请补充要查询的文档名称或具体条款。'

# Routes whose answers may be LLM-condensed prose (numbers/dates stay verbatim)
# instead of strict verbatim excerpts.
SUMMARIZING_ROUTES = {'summary', 'compare'}


class QueryState(TypedDict):
    tenant_id: str
    run_id: str


def keyword_recall(db, tenant, user, question, kb_ids, limit):
    # Filter before computing document frequencies: other users' data must not
    # influence ranking, and stale/deleted versions must not enter the corpus.
    rows = authorized_chunks(db, tenant, user, kb_ids)
    return bm25_rank(question, [(c.id, c.chunk_text) for c, _ in rows], limit)


def optional_mcp_search(config, question):
    """Call one administrator-configured MCP search tool; returned text remains untrusted.

    External results do not become internal citations and do not change KB authorization.
    """
    if not config.web_search_enabled or not config.mcp_search_url:
        return {'status': 'disabled'}
    headers = {'Accept': 'application/json, text/event-stream',
               'Authorization': f'Bearer {config.mcp_search_token}'}
    with httpx.Client(timeout=15, follow_redirects=False) as client:
        response = client.post(config.mcp_search_url, headers=headers, json={
            'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
                'protocolVersion': '2025-03-26', 'capabilities': {},
                'clientInfo': {'name': 'zhanggui', 'version': '0.1.0'}}})
        response.raise_for_status()
        if response.headers.get('mcp-session-id'):
            headers['Mcp-Session-Id'] = response.headers['mcp-session-id']
        client.post(config.mcp_search_url, headers=headers,
                    json={'jsonrpc': '2.0', 'method': 'notifications/initialized'})
        response = client.post(config.mcp_search_url, headers=headers, json={'jsonrpc': '2.0', 'id': 2,
            'method': 'tools/call', 'params': {'name': 'search', 'arguments': {'query': question}}})
        response.raise_for_status()
        if len(response.content) > 1024 * 1024:
            raise ValueError('MCP_RESPONSE_TOO_LARGE')
        if 'text/event-stream' in response.headers.get('content-type', ''):
            messages = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
            body = next((x for x in messages if x.get('id') == 2), {})
        else:
            body = response.json()
        if 'error' in body:
            raise ValueError('MCP_SEARCH_FAILED')
        return {'status': 'received', 'untrusted_result': body.get('result', {})}


def process_query(tenant, run_id, config, bind=None, checkpointer=None, services=None):
    services = services or Services(config)
    context = {'hits': {}, 'timings': {}, 'degraded': [], 'candidates': [], 'selected': [], 'usage': {}}

    def tx():
        return transaction(tenant, bind=bind)

    def active(db):
        run = scoped(db, QueryRun, tenant, run_id)
        if not run or run.cancel_requested or run.status == 'CANCELLED':
            raise InterruptedError('CANCELLED')
        return run

    def stage(name, fn):
        def invoke(state):
            start = time.monotonic()
            if name == 'stream_result':
                return {}  # SSE reads committed events; completion must remain the last event.
            with tx() as db:
                active(db)
                append_event(db, tenant, run_id, 'run.stage', {'stage': name})
            fn()
            context['timings'][name] = round((time.monotonic() - start) * 1000, 3)
            return {}
        return invoke

    def authorize_scope():
        with tx() as db:
            run = active(db)
            context.update(question=run.question, kb_ids=run.kb_ids, user=run.user_id,
                           session_id=run.session_id, web_requested=run.web_search_requested)
            for kb in run.kb_ids:
                require_kb(db, tenant, run.user_id, kb)
            docs = db.scalars(select(Document).where(Document.tenant_id == tenant, Document.kb_id.in_(run.kb_ids),
                Document.deleted_at.is_(None), Document.current_version_id.is_not(None))).all()
            context['versions'] = [d.current_version_id for d in docs if document_allowed(d, run.user_id)]
            run.status = 'RUNNING'

    def classify_query():
        context['route'] = classify(context['question'])

    def rewrite_query():
        # Deterministic whitespace normalization never expands KB scope or invents entities.
        context['query'] = re.sub(r'\s+', ' ', context['question']).strip()

    def retrieve_vectors():
        try:
            if not context['versions']:
                context['hits']['vector'] = []
                return
            embedding = services.embed([context['query']])[0]
            context['hits']['vector'] = services.vectors.search(embedding, tenant, context['kb_ids'],
                context['versions'], context['user'], config.vector_top_k)
        except Exception:
            context['hits']['vector'] = []
            context['degraded'].append('vector_unavailable')

    def retrieve_keywords():
        with tx() as db:
            context['hits']['keyword'] = keyword_recall(db, tenant, context['user'], context['query'],
                                                       context['kb_ids'], config.keyword_top_k)

    def retrieve_graph():
        try:
            context['hits']['graph'] = services.graph.search(context['query'], tenant, context['kb_ids'],
                context['versions'], context['user'], config.graph_top_k, config.graph_max_hops) if context['versions'] else []
        except Exception:
            context['hits']['graph'] = []
            context['degraded'].append('graph_unavailable')

    def fuse_candidates():
        fused = fuse(context['hits'], config.rrf_k)[:config.rerank_candidates]
        with tx() as db:
            allowed = {c.id: (c, d) for c, d in authorized_chunks(db, tenant, context['user'],
                          context['kb_ids'], [row['chunk_id'] for row in fused])}
            candidates = []
            for row in fused:
                if row['chunk_id'] not in allowed:
                    continue
                chunk, doc = allowed[row['chunk_id']]
                candidates.append({**row, 'document_id': doc.id, 'document_name': doc.name,
                    'version_id': chunk.version_id, 'chunk_text': chunk.chunk_text,
                    'page_start': chunk.page_start, 'page_end': chunk.page_end,
                    'section_path': chunk.section_path, 'content_type': chunk.content_type, 'rerank_score': None})
            context['candidates'] = candidates

    def rerank_candidates():
        candidates = context['candidates']
        if not candidates:
            return
        try:
            scores = services.models.rerank(context['query'], [x['chunk_text'] for x in candidates])
            for row, score in zip(candidates, scores):
                row['rerank_score'] = score
            candidates.sort(key=lambda row: row['rerank_score'], reverse=True)
        except Exception:
            context['degraded'].append('reranker_rrf_fallback')
            # RRF ordering is kept; evidence gate refuses uncalibrated scores by default.

    def build_context():
        context['selected'] = select_context(context['candidates'], config.final_context_chunks,
                                             config.max_chunks_per_document, context['route'])

    def check_evidence():
        context['gate'] = evidence_gate(context['query'], context['selected'], config.evidence_min_score)

    def external_search():
        if not context['gate']['sufficient'] and context['web_requested'] and config.web_search_enabled:
            try:
                external = optional_mcp_search(config, context['query'])
                # Saved as a separate source, never assigned a fabricated internal chunk_id.
                context['external_status'] = external['status']
            except Exception:
                context['degraded'].append('web_unavailable')

    def generate_answer():
        context['raw_answer'] = {'claims': []}
        if context['gate']['sufficient']:
            context['raw_answer'], context['usage'] = services.models.generate(
                context['query'], context['selected'], summarize=context['route'] in SUMMARIZING_ROUTES)

    def verify_citations():
        if context['route'] in SUMMARIZING_ROUTES:
            valid, claims = verify_summary_claims(context['raw_answer'], context['selected'])
        else:
            valid, claims = verify_claims(context['raw_answer'], context['selected'])
        context['claims'] = claims if valid else []
        context['refused'] = not valid

    def persist_answer():
        with tx() as db:
            # Consistent order: documents first, then run. Deletion holds the same document row lock.
            document_ids = sorted({row['document_id'] for row in context['selected']})
            if document_ids:
                list(db.scalars(select(Document).where(Document.tenant_id == tenant,
                    Document.id.in_(document_ids)).order_by(Document.id).with_for_update()))
            # Lock during final authorization + commit to serialize with concurrent cancellation.
            run = db.scalar(select(QueryRun).where(QueryRun.tenant_id == tenant,
                            QueryRun.id == run_id).with_for_update())
            if run.cancel_requested or run.status == 'CANCELLED':
                raise InterruptedError('CANCELLED')
            chosen_ids = {identity for claim in context['claims'] for identity in claim['chunk_ids']}
            allowed = {c.id for c, _ in authorized_chunks(db, tenant, context['user'], context['kb_ids'], list(chosen_ids))}
            if allowed != chosen_ids:
                context['claims'], context['refused'] = [], True
            for model in [Message, AnswerCitation, RetrievalCandidate]:
                stmt = delete(model).where(model.tenant_id == tenant, model.run_id == run_id)
                if model is Message:
                    stmt = stmt.where(Message.role == 'assistant')
                db.execute(stmt)
            selected = {x['chunk_id'] for x in context['selected']}
            for row in context['candidates']:
                db.add(RetrievalCandidate(tenant_id=tenant, run_id=run_id, chunk_id=row['chunk_id'],
                    sources=row['sources'], rrf_score=row['rrf_score'], rerank_score=row['rerank_score'],
                    selected=row['chunk_id'] in selected))
            by_id = {x['chunk_id']: x for x in context['selected']}
            citation_ids = list(dict.fromkeys(cid for claim in context['claims'] for cid in claim['chunk_ids']))
            for cid in citation_ids:
                row = by_id[cid]
                citation = {key: row[key] for key in ['document_id', 'document_name', 'page_start', 'page_end', 'section_path']}
                citation.update(answer_version=1, document_version=row['version_id'], chunk_id=cid,
                                source_excerpt=row['chunk_text'])
                db.add(AnswerCitation(tenant_id=tenant, run_id=run_id, **citation))
                append_event(db, tenant, run_id, 'citation.ready', citation)
            answer = '\n'.join(claim['text'] + ' ' + ''.join(f'[{citation_ids.index(cid)+1}]' for cid in claim['chunk_ids'])
                               for claim in context['claims']) or REFUSAL
            db.add(Message(tenant_id=tenant, session_id=context['session_id'], run_id=run_id,
                           role='assistant', content=answer))
            # All visible answer chunks have already passed citation verification.
            for offset in range(0, len(answer), 80):
                append_event(db, tenant, run_id, 'answer.delta', {'text': answer[offset:offset+80]})
            append_event(db, tenant, run_id, 'answer.replace', {'text': answer})
            run.route, run.token_usage, run.timings = context['route'], context['usage'], context['timings']
            run.retrieval_config = {**run.retrieval_config, 'degraded': context['degraded'], 'evidence': context['gate']}
            run.status = 'COMPLETED'
            run.error_code = 'INSUFFICIENT_EVIDENCE' if context['refused'] else None
            append_event(db, tenant, run_id, 'run.completed', {'status': run.status, 'error_code': run.error_code})

    graph = StateGraph(QueryState)
    functions = {'authorize_scope': authorize_scope, 'classify_query': classify_query, 'rewrite_query': rewrite_query,
        'retrieve_vectors': retrieve_vectors, 'retrieve_keywords': retrieve_keywords, 'retrieve_graph': retrieve_graph,
        'fuse_candidates': fuse_candidates, 'rerank_candidates': rerank_candidates, 'build_context': build_context,
        'check_evidence': check_evidence, 'optional_web_search': external_search, 'generate_answer': generate_answer,
        'verify_citations': verify_citations, 'persist_answer': persist_answer, 'stream_result': lambda: None}
    for name, fn in functions.items():
        graph.add_node(name, stage(name, fn))
    graph.add_edge(START, 'authorize_scope')
    graph.add_edge('authorize_scope', 'classify_query'); graph.add_edge('classify_query', 'rewrite_query')
    recall = ['retrieve_vectors', 'retrieve_keywords', 'retrieve_graph']
    for node in recall:
        graph.add_edge('rewrite_query', node)
    graph.add_edge(recall, 'fuse_candidates')
    tail = list(functions)[6:]
    for a, b in zip(tail, tail[1:]):
        graph.add_edge(a, b)
    graph.add_edge('stream_result', END)
    try:
        with tx() as db:
            run = active(db)
            if run.status == 'COMPLETED':
                return
        graph.compile(checkpointer=checkpointer).invoke({'tenant_id': tenant, 'run_id': run_id},
            {'configurable': {'thread_id': f'{tenant}:query:{run_id}'}})
    finally:
        services.close()
