from uuid import uuid4
import pytest


def test_rrf_preserves_provenance_and_sums_ranks():
    from app.retrieval import fuse
    result = fuse({'vector': [('a', .9), ('b', .8)], 'keyword': [('b', 4)]}, 60)
    assert result[0]['chunk_id'] == 'b'
    assert result[0]['rrf_score'] == pytest.approx(1/62 + 1/61)
    assert result[0]['sources']['keyword']['rank'] == 1


def test_scope_filter_rejects_injection_and_contains_all_boundaries():
    from app.adapters.stores import vector_filter
    tenant, kb, version, user = [str(uuid4()) for _ in range(4)]
    result = vector_filter(tenant, [kb], [version], user)
    assert all(x in result for x in ['tenant_id', 'kb_id', 'version_id', 'acl_scope'])
    with pytest.raises(ValueError):
        vector_filter('x" or true', [kb], [version], user)


def test_context_diversity_and_evidence_gate():
    from app.retrieval import select_context, evidence_gate
    candidates = [{'chunk_id': str(i), 'document_id': 'a' if i < 6 else 'b',
                   'chunk_text': f'合同 ABC123 金额100元 条款{i}', 'rerank_score': .95 - i/100,
                   'sources': {'vector': {}, 'keyword': {}}} for i in range(10)]
    selected = select_context(candidates, 10, 4)
    assert len([x for x in selected if x['document_id'] == 'a']) == 4
    assert evidence_gate('合同 ABC123 金额', selected)['sufficient']
    assert not evidence_gate('合同 XYZ999 金额', selected)['sufficient']
    assert not evidence_gate('问题', [])['sufficient']


def test_citations_reject_fabricated_claims_and_unknown_ids():
    from app.retrieval import verify_claims
    evidence = [{'chunk_id': 'a', 'chunk_text': '合同金额为100元。'}]
    good = {'claims': [{'text': '合同金额为100元。', 'chunk_ids': ['a']}]}
    assert verify_claims(good, evidence)[0]
    assert not verify_claims({'claims': [{'text': '合同金额为200元。', 'chunk_ids': ['a']}]}, evidence)[0]
    assert not verify_claims({'claims': [{'text': '合同金额为100元。', 'chunk_ids': ['evil']}]}, evidence)[0]


def test_graph_edges_require_evidence():
    from app.adapters.models import extract_graph
    from types import SimpleNamespace
    chunk = SimpleNamespace(id='a', chunk_text='[甲公司] --供应--> [乙公司]。', chunk_no=0)
    graph = extract_graph([chunk])
    assert graph['relations'][0]['source_chunk_id'] == 'a'
    assert graph['relations'][0]['type'] == '供应'
    plain = extract_graph([SimpleNamespace(id='b', chunk_text='甲乙之间未知。', chunk_no=0)])
    assert plain['relations'] == []


def test_summary_claims_allow_reworded_prose_but_require_verbatim_numbers():
    from app.retrieval import verify_summary_claims
    evidence = [{'chunk_id': 'a', 'chunk_text': '中国的非传染性疾病死亡占总死亡人数的88.5%。'}]
    good = {'claims': [{'text': '据材料，非传染性疾病死亡占中国总死亡的88.5%。', 'chunk_ids': ['a']}]}
    ok, verified = verify_summary_claims(good, evidence)
    assert ok and verified[0]['text'] == good['claims'][0]['text']
    # 数字被改动 -> 拒绝
    assert not verify_summary_claims({'claims': [{'text': '占比为88.6%。', 'chunk_ids': ['a']}]}, evidence)[0]
    # 缺少引用 / 引用不存在 -> 拒绝
    assert not verify_summary_claims({'claims': [{'text': '占比为88.5%。', 'chunk_ids': []}]}, evidence)[0]
    assert not verify_summary_claims({'claims': [{'text': '占比为88.5%。', 'chunk_ids': ['evil']}]}, evidence)[0]
