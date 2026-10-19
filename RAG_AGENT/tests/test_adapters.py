import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from app.adapters.models import ModelClient
from app.adapters.stores import VectorStore
from app.ingestion import IngestionError


def test_delete_covers_owned_historical_model_collections_only():
    store = VectorStore.__new__(VectorStore)
    store.config = SimpleNamespace(milvus_collection='knowledge')
    calls = []
    store.client = SimpleNamespace(list_collections=lambda: ['knowledge_aaaaaaaaaaaa_3', 'knowledge_bbbbbbbbbbbb_1024',
                                                            'other_aaaaaaaaaaaa_3', 'knowledge_untrusted'],
        delete=lambda collection, **kwargs: calls.append((collection, kwargs)))
    tenant, version = str(uuid4()), str(uuid4())
    store.delete(tenant, version)
    assert [x[0] for x in calls] == ['knowledge_aaaaaaaaaaaa_3', 'knowledge_bbbbbbbbbbbb_1024']
    assert all(tenant in x[1]['filter'] and version in x[1]['filter'] for x in calls)


def test_current_version_reconcile_does_not_accept_historical_vectors():
    store = VectorStore.__new__(VectorStore)
    store.config = SimpleNamespace(milvus_collection='knowledge')
    store.collection = 'knowledge_bbbbbbbbbbbb_1024'
    def iterator(collection, **kwargs):
        batches = iter([[{'chunk_id': 'old'}], []] if collection.endswith('_3') else [[]])
        return SimpleNamespace(next=lambda: next(batches), close=lambda: None)
    store.client = SimpleNamespace(list_collections=lambda: ['knowledge_aaaaaaaaaaaa_3', store.collection],
                                   query_iterator=iterator)
    assert store.ids(str(uuid4()), str(uuid4())) == set()
    assert store.ids(str(uuid4())) == {'old'}


def _model_client(handler, batch_size=4, dimension=4):
    config = SimpleNamespace(embedding_base_url='http://embed/v1', embedding_api_key='',
        embedding_model='bge-m3', embedding_dimension=dimension, embedding_batch_size=batch_size,
        reranker_base_url='http://rerank/', reranker_timeout_seconds=10,
        llm_base_url='http://llm/v1', llm_api_key='k', llm_model='m')
    client = ModelClient(config)
    client.http = httpx.Client(transport=httpx.MockTransport(handler))
    return client


def _embedding_body(ids, dim=4):
    return {'data': [{'index': i, 'embedding': [float(i)] * dim} for i in ids]}


def test_embed_reduces_batch_when_server_rejects_large_batch():
    sizes = []

    def handler(request):
        n = len(json.loads(request.content)['input'])
        sizes.append(n)
        if n > 2:
            return httpx.Response(429, json={'message': 'Model is overloaded', 'type': 'Overloaded'})
        return httpx.Response(200, json=_embedding_body(range(n)))

    vectors = _model_client(handler, batch_size=4).embed(['a', 'b', 'c', 'd'])
    assert sizes == [4, 2, 2]
    assert [v[0] for v in vectors] == [0.0, 1.0, 0.0, 1.0]


def test_embed_single_input_still_too_large_is_retryable():
    def handler(request):
        return httpx.Response(429, json={'message': 'Model is overloaded'})

    with pytest.raises(IngestionError) as exc:
        _model_client(handler, batch_size=1).embed(['a'])
    assert exc.value.error_code == 'EMBEDDING_FAILED'
    assert exc.value.retryable is True


def test_embed_malformed_response_is_not_retryable():
    def handler(request):
        return httpx.Response(200, json={'data': [{'index': 0, 'embedding': [1.0] * 5}]})

    with pytest.raises(IngestionError) as exc:
        _model_client(handler, batch_size=4, dimension=4).embed(['a'])
    assert exc.value.error_code == 'EMBEDDING_FAILED'
    assert exc.value.retryable is False


def test_embed_other_http_error_is_retryable():
    def handler(request):
        return httpx.Response(500, json={})

    with pytest.raises(IngestionError) as exc:
        _model_client(handler, batch_size=4).embed(['a'])
    assert exc.value.error_code == 'EMBEDDING_FAILED'
    assert exc.value.retryable is True


def test_rerank_splits_batch_over_32():
    sizes = []

    def handler(request):
        body = json.loads(request.content)
        texts = body['texts']
        sizes.append(len(texts))
        return httpx.Response(200, json=[{'index': i, 'score': float(t)} for i, t in enumerate(texts)])

    texts = [str(i) for i in range(40)]
    scores = _model_client(handler).rerank('q', texts)
    assert sizes == [32, 8]
    assert scores == [float(i) for i in range(40)]


def test_rerank_reduces_batch_on_429():
    sizes = []

    def handler(request):
        body = json.loads(request.content)
        texts = body['texts']
        sizes.append(len(texts))
        if len(texts) > 2:
            return httpx.Response(429, json={'error': 'Model is overloaded'})
        return httpx.Response(200, json=[{'index': i, 'score': float(t)} for i, t in enumerate(texts)])

    scores = _model_client(handler).rerank('q', ['0', '1', '2', '3'])
    assert sizes == [4, 2, 2]
    assert scores == [0.0, 1.0, 2.0, 3.0]


def test_rerank_single_input_still_too_large_is_retryable():
    def handler(request):
        return httpx.Response(429, json={'error': 'Model is overloaded'})

    with pytest.raises(IngestionError) as exc:
        _model_client(handler).rerank('q', ['x'])
    assert exc.value.error_code == 'RERANK_FAILED'
    assert exc.value.retryable is True


def test_generate_selects_summary_prompt_when_summarizing():
    from app.adapters.models import SUMMARY_SYSTEM_PROMPT, SYSTEM_PROMPT
    captured = {}

    def handler(request):
        body = json.loads(request.content)
        captured['prompt'] = body['messages'][0]['content']
        return httpx.Response(200, json={'choices': [{'message': {'content': '{"claims": []}'}}], 'usage': {}})

    client = _model_client(handler)
    client.generate('总结一下', [{'chunk_id': 'a', 'chunk_text': 'x'}], summarize=True)
    assert captured['prompt'] == SUMMARY_SYSTEM_PROMPT
    client.generate('多少钱', [{'chunk_id': 'a', 'chunk_text': 'x'}])
    assert captured['prompt'] == SYSTEM_PROMPT
