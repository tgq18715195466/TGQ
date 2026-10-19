"""Versioned evaluation; unknown metrics remain null, never fabricated as successes."""
import math
import time

from sqlalchemy import delete, select

from app.db import transaction
from app.models import (AnswerCitation, ChatSession, Document, EvalCase, EvalResult, EvalRun,
                        Message, QueryRun, RetrievalCandidate)
from app.repository import require_kb, scoped

BADCASES = ['parse_failure', 'ocr_failure', 'heading_failure', 'table_failure', 'chunk_failure',
            'permission_failure', 'retrieval_failure', 'ranking_failure', 'context_failure',
            'generation_failure', 'citation_failure', 'abstention_failure', 'latency_failure']


def retrieval_metrics(retrieved, expected, k=10):
    actual, gold = list(dict.fromkeys(retrieved))[:k], set(expected)
    return {f'recall_at_{k}': len(set(actual) & gold) / len(gold) if gold else None,
            f'mrr_at_{k}': next((1 / i for i, value in enumerate(actual, 1) if value in gold), 0.0) if gold else None,
            'context_precision': len(set(actual) & gold) / len(actual) if actual and gold else None}


def character_accuracy(expected, actual):
    if not expected:
        return None
    previous = list(range(len(actual) + 1))
    for i, left in enumerate(expected, 1):
        row = [i]
        for j, right in enumerate(actual, 1):
            row.append(min(row[-1]+1, previous[j]+1, previous[j-1] + (left != right)))
        previous = row
    return max(0.0, 1 - previous[-1] / len(expected))


def structure_f1(expected, actual):
    gold, predicted = set(expected), set(actual)
    return 2 * len(gold & predicted) / (len(gold) + len(predicted)) if gold or predicted else None


def percentile(values, percentile):
    return sorted(values)[max(0, math.ceil(len(values)*percentile/100)-1)] if values else None


def evaluate_prediction(case, prediction):
    expected = case.get('evidence_chunk_ids', [])
    result = retrieval_metrics(prediction.get('retrieved_chunk_ids', []), expected)
    cited = prediction.get('cited_chunk_ids', [])
    result.update(faithfulness=None, citation_precision=(len(set(cited) & set(expected)) / len(set(cited))
        if cited and expected else None), citation_coverage=prediction.get('citation_coverage'),
        abstention_correct=(int(bool(prediction.get('refused'))) if case.get('question_type') == 'unanswerable' else None),
        latency_ms=prediction.get('latency_ms'), success=int(prediction.get('status') == 'COMPLETED'),
        character_accuracy=character_accuracy(case['expected_text'], prediction.get('parsed_text', ''))
            if 'expected_text' in case and 'parsed_text' in prediction else None,
        heading_f1=structure_f1(case['expected_headings'], prediction['headings'])
            if 'expected_headings' in case and 'headings' in prediction else None,
        table_cell_f1=structure_f1(case['expected_cells'], prediction['cells'])
            if 'expected_cells' in case and 'cells' in prediction else None,
        permission_leaks=prediction.get('permission_leaks'))
    return result


def ragas_scores(cases, predictions, config):
    # Optional dependency; failure is reported, not silently converted to zero/perfect metrics.
    from datasets import Dataset
    from ragas import evaluate
    from ragas.metrics import faithfulness, answer_relevancy
    from langchain_openai import ChatOpenAI, OpenAIEmbeddings
    rows = [{'question': c['question'], 'answer': p['answer'], 'contexts': p['contexts'],
             'ground_truth': c.get('expected_answer', '')} for c, p in zip(cases, predictions)]
    llm = ChatOpenAI(model=config.llm_model, api_key=config.llm_api_key or 'configured', base_url=config.llm_base_url)
    embeddings = OpenAIEmbeddings(model=config.embedding_model, api_key=config.embedding_api_key or 'configured',
                                  base_url=config.embedding_base_url)
    return evaluate(Dataset.from_list(rows), metrics=[faithfulness, answer_relevancy],
                    llm=llm, embeddings=embeddings).to_pandas().to_dict(orient='records')


def process_eval(tenant, run_id, config, bind=None, services_factory=None):
    from app.query import process_query
    def tx():
        return transaction(tenant, bind=bind)
    with tx() as db:
        run = scoped(db, EvalRun, tenant, run_id)
        if not run or run.status == 'COMPLETED':
            return
        for kb in run.config_jsonb['kb_ids']:
            require_kb(db, tenant, run.user_id, kb, admin=True)
        cases = [(x.id, x.data_jsonb) for x in db.scalars(select(EvalCase).where(
            EvalCase.tenant_id == tenant, EvalCase.dataset_id == run.dataset_id))]
        if not cases:
            run.status, run.error_code = 'FAILED', 'EMPTY_DATASET'
            return
        kb_ids, user_id, use_ragas = run.config_jsonb['kb_ids'], run.user_id, run.config_jsonb.get('use_ragas', False)
        run.status = 'RUNNING'
        session = ChatSession(tenant_id=tenant, user_id=user_id, title='评测')
        db.add(session); db.flush()
        session_id = session.id
    all_metrics, predictions = [], []
    try:
        for case_id, case in cases:
            with tx() as db:
                query = QueryRun(tenant_id=tenant, session_id=session_id, user_id=user_id, question=case['question'],
                    kb_ids=kb_ids, model_version=config.llm_model, prompt_version=config.prompt_version,
                    retrieval_config=config.retrieval_snapshot())
                db.add(query); db.flush()
                query_id = query.id
            started = time.monotonic()
            process_query(tenant, query_id, config, bind, services=services_factory() if services_factory else None)
            with tx() as db:
                document_ids = list(db.scalars(select(AnswerCitation.document_id).where(
                    AnswerCitation.tenant_id == tenant, AnswerCitation.run_id == query_id)))
                if document_ids:
                    list(db.scalars(select(Document).where(Document.tenant_id == tenant,
                        Document.id.in_(document_ids)).order_by(Document.id).with_for_update()))
                query = scoped(db, QueryRun, tenant, query_id)
                answer = db.scalar(select(Message.content).where(Message.tenant_id == tenant,
                    Message.run_id == query_id, Message.role == 'assistant')) or ''
                candidates = list(db.scalars(select(RetrievalCandidate).where(RetrievalCandidate.tenant_id == tenant,
                    RetrievalCandidate.run_id == query_id).order_by(RetrievalCandidate.rrf_score.desc())))
                citations = list(db.scalars(select(AnswerCitation).where(AnswerCitation.tenant_id == tenant,
                    AnswerCitation.run_id == query_id)))
                prediction = {'answer': answer, 'retrieved_chunk_ids': [x.chunk_id for x in candidates],
                    'cited_chunk_ids': [x.chunk_id for x in citations], 'contexts': [x.source_excerpt for x in citations],
                    'refused': query.error_code == 'INSUFFICIENT_EVIDENCE', 'status': query.status,
                    'latency_ms': round((time.monotonic()-started)*1000, 2),
                    'citation_coverage': 1.0 if citations else None}
                prediction.update(query_run_id=query_id, document_ids=list({x.document_id for x in citations}))
                metrics = evaluate_prediction(case, prediction)
                badcase = 'abstention_failure' if metrics['abstention_correct'] == 0 else (
                    'retrieval_failure' if metrics['recall_at_10'] is not None and metrics['recall_at_10'] < .85 else None)
                db.execute(delete(EvalResult).where(EvalResult.tenant_id == tenant,
                                                    EvalResult.run_id == run_id, EvalResult.case_id == case_id))
                db.add(EvalResult(tenant_id=tenant, run_id=run_id, case_id=case_id, metrics_jsonb=metrics,
                                   prediction_jsonb=prediction, badcase=badcase))
                predictions.append(prediction); all_metrics.append(metrics)
        if use_ragas:
            scored = ragas_scores([case for _, case in cases], predictions, config)
            with tx() as db:
                for (case_id, _), extra, metrics in zip(cases, scored, all_metrics):
                    sanitized = {key: float(value) if isinstance(value, (int, float)) and math.isfinite(value)
                                 else None for key, value in extra.items() if key in {'faithfulness', 'answer_relevancy'}}
                    metrics.update(sanitized)
                    row = db.scalar(select(EvalResult).where(EvalResult.tenant_id == tenant,
                        EvalResult.run_id == run_id, EvalResult.case_id == case_id))
                    row.metrics_jsonb = metrics
        summary = {}
        for name in all_metrics[0]:
            values = [row[name] for row in all_metrics if row.get(name) is not None]
            summary[name] = sum(values)/len(values) if values else None
        summary['latency_p95_ms'] = percentile([p['latency_ms'] for p in predictions], 95)
        with tx() as db:
            run = scoped(db, EvalRun, tenant, run_id)
            run.status, run.metrics_jsonb = 'COMPLETED', {'case_count': len(cases), **summary}
    except Exception as exc:
        with tx() as db:
            run = scoped(db, EvalRun, tenant, run_id)
            run.status, run.error_code = 'FAILED', 'EVALUATION_DEPENDENCY_FAILED'
        raise RuntimeError('Evaluation failed; no unmeasured scores have been recorded') from exc
