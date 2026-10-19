"""Deterministic fusion, evidence checks and conservative citation verification."""
import re
from collections import Counter


def fuse(results, k=60):
    merged = {}
    for source, hits in results.items():
        seen = set()
        for rank, (identity, score) in enumerate(hits, 1):
            if identity in seen:
                continue
            seen.add(identity)
            row = merged.setdefault(identity, {'chunk_id': identity, 'sources': {}, 'rrf_score': 0.0})
            row['sources'][source] = {'rank': rank, 'score': float(score)}
            row['rrf_score'] += 1 / (k + rank)
    return sorted(merged.values(), key=lambda x: (-x['rrf_score'], x['chunk_id']))


def classify(question):
    for route, words in [('compare', ['比较', '对比', '区别']), ('graph', ['关系', '关联', '上下游']),
                         ('table', ['表格', '第几行', '单价', '合计']), ('latest', ['最新', '今天', '目前']),
                         ('summary', ['总结', '概括', '解释'])]:
        if any(word in question for word in words):
            return route
    return 'fact'


def select_context(candidates, limit=10, per_document=4, route='fact'):
    selected, documents, texts = [], Counter(), set()
    if route in {'summary', 'compare'}:
        first, rest, seen_groups = [], [], set()
        for row in candidates:
            group = row['document_id'] if route == 'compare' else (row['document_id'], tuple(row.get('section_path', [])))
            (rest if group in seen_groups else first).append(row)
            seen_groups.add(group)
        candidates = first + rest
    elif route == 'table':
        candidates = sorted(candidates, key=lambda row: (row.get('rerank_score') or 0)
                            + (0.05 if row.get('content_type') == 'table' else 0), reverse=True)
    # A hard per-document cap and exact-body dedup prevent repetitive evidence.
    for item in candidates:
        normalized = re.sub(r'\s+', '', item['chunk_text'])
        if normalized in texts or documents[item['document_id']] >= per_document:
            continue
        selected.append(item)
        texts.add(normalized)
        documents[item['document_id']] += 1
        if len(selected) >= min(12, limit + (2 if route == 'compare' else 0)):
            break
    return selected


def evidence_gate(question, evidence, minimum=.5):
    if not evidence:
        return {'sufficient': False, 'reason': 'no_evidence'}
    scores = [x.get('rerank_score') for x in evidence[:3]]
    scores = [x for x in scores if x is not None]
    top = max(scores, default=0)
    mean = sum(scores) / max(1, len(scores))
    agreement = max((len(x['sources']) for x in evidence), default=0)
    required = set(re.findall(r'[A-Za-z]+[A-Za-z0-9_-]*\d[A-Za-z0-9_-]*|\d+(?:\.\d+)?', question))
    body = '\n'.join(x['chunk_text'] for x in evidence)
    entity_coverage = all(value.casefold() in body.casefold() for value in required)
    # Detect opposed statements about identical prefixes, do not choose one side silently.
    conflict = any(f'{term}不' in body and f'{term}可' in body for term in ['应', '允许', '可以'])
    sufficient = bool(scores and top >= minimum and mean >= minimum * .75 and entity_coverage)
    return {'sufficient': sufficient, 'top_score': top, 'top3_mean': mean, 'recall_agreement': agreement,
            'entity_coverage': entity_coverage, 'conflict_detected': conflict,
            'reason': 'supported' if sufficient else 'insufficient_coverage_or_score'}


def verify_claims(answer, evidence):
    """MVP strict extractive mode: a claim must occur verbatim in every cited source.

    This intentionally rejects plausible paraphrases; it never treats citation presence
    as proof of factual support. Historical snapshots use these verified excerpts.
    """
    sources = {row['chunk_id']: row['chunk_text'] for row in evidence}
    claims = answer.get('claims', []) if isinstance(answer, dict) else []
    if not isinstance(claims, list) or not claims or len(claims) > 30:
        return False, []
    verified = []
    for claim in claims:
        if not isinstance(claim, dict):
            return False, []
        sentence, ids = claim.get('text', ''), claim.get('chunk_ids', [])
        if not isinstance(sentence, str) or not sentence.strip() or not isinstance(ids, list) or not ids:
            return False, []
        if any(not isinstance(cid, str) or cid not in sources or sentence not in sources[cid] for cid in ids):
            return False, []
        verified.append({'text': sentence, 'chunk_ids': list(dict.fromkeys(ids))})
    return True, verified


def verify_summary_claims(answer, evidence):
    """Grounded verification for summarizing routes.

    Prose may be reworded, but every number, percentage or year in a claim must
    appear verbatim in at least one cited chunk, and every claim must cite an
    existing chunk. This allows condensation while blocking invented figures.
    """
    sources = {row['chunk_id']: row['chunk_text'] for row in evidence}
    claims = answer.get('claims', []) if isinstance(answer, dict) else []
    if not isinstance(claims, list) or not claims or len(claims) > 30:
        return False, []
    verified = []
    for claim in claims:
        if not isinstance(claim, dict):
            return False, []
        sentence, ids = claim.get('text', ''), claim.get('chunk_ids', [])
        if not isinstance(sentence, str) or not sentence.strip() or not isinstance(ids, list) or not ids:
            return False, []
        cited = [sources[cid] for cid in ids if cid in sources]
        if not cited:
            return False, []
        facts = re.findall(r'\d+(?:\.\d+)?%?|\d{4}年', sentence)
        body = '\n'.join(cited)
        if any(fact not in body for fact in facts):
            return False, []
        verified.append({'text': sentence, 'chunk_ids': list(dict.fromkeys(ids))})
    return True, verified
