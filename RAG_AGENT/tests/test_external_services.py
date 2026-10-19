"""Opt-in real-store smoke tests. Never silently replace real services with fakes."""
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.adapters import Services
from app.config import settings

pytestmark = pytest.mark.integration


@pytest.mark.skipif(os.getenv('RUN_EXTERNAL_INTEGRATION') != '1', reason='MinerU, model endpoints, Milvus and Neo4j not configured')
def test_real_vector_graph_write_recall_delete():
    c = settings(); services = Services(c)
    tenant, kb, doc, version, cid, user = [str(uuid4()) for _ in range(6)]
    chunk = SimpleNamespace(id=cid, tenant_id=tenant, kb_id=kb, document_id=doc, version_id=version,
        content_type='text', page_start=1, page_end=1, chunk_no=0,
        chunk_text='[甲公司] --供应--> [乙公司]', embedding_text='[甲公司] --供应--> [乙公司]')
    document = SimpleNamespace(id=doc, acl_jsonb={})
    try:
        embedding = services.embed([chunk.embedding_text])
        services.sync_vectors(tenant, version, [chunk], [document], 'test', embedding)
        services.sync_graph(tenant, version, [chunk], [document], 'test')
        assert cid in services.vector_ids(tenant, version)
        assert cid in services.graph_ids(tenant, version)
        assert cid in {identity for identity, score in services.vectors.search(embedding[0], tenant, [kb], [version], user, 5)}
        assert cid in {identity for identity, score in services.graph.search('甲公司', tenant, [kb], [version], user, 5)}
        assert not services.vectors.search(embedding[0], str(uuid4()), [kb], [version], user, 5)
    finally:
        services.delete_vectors(tenant, version, 'test'); services.delete_graph(tenant, version, 'test')
        assert not services.vector_ids(tenant, version)
        assert not services.graph_ids(tenant, version)
        services.close()


@pytest.mark.skipif(os.getenv('RUN_MINERU_INTEGRATION') != '1', reason='Real MinerU API is not configured')
def test_real_mineru_normalized_text():
    import asyncio
    from app.ingestion import validate_file, normalize_file, MinerUClient
    data = '合同 A-001 金额为100元。'.encode()
    pdf = normalize_file(data, validate_file(data, 'fixture.txt', 'text/plain'))
    parsed = asyncio.run(MinerUClient(settings().mineru_base_url).parse(pdf.pdf, str(uuid4())))
    assert parsed.page_count == pdf.page_count
    assert '100' in ''.join(block.text for block in parsed.blocks)
