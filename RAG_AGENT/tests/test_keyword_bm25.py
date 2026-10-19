import math
from types import SimpleNamespace

import pytest


def test_bm25_known_score_partial_match_and_stable_order():
    from app.keyword import bm25_rank
    rows = [('b', '预算 预算'), ('a', '预算 预算'), ('c', '其他 内容')]
    hits = bm25_rank('预算 未知词', rows, 10)
    assert [x[0] for x in hits] == ['a', 'b']
    assert hits[0][1] == pytest.approx(math.log(1 + 1.5 / 2.5) * 2 * 2.2 / 3.2)
    assert bm25_rank('!!!', rows, 10) == []
    assert bm25_rank('预算', rows, 0) == []


def test_bm25_length_normalization_and_chinese():
    from app.keyword import bm25_rank
    hits = bm25_rank('经费预算如何分配', [('long', '经费预算 ' + '其他 ' * 30),
                                         ('short', '经费预算'), ('none', '天气晴朗')], 10)
    assert hits[0][0] == 'short'
    assert {x[0] for x in hits} == {'long', 'short'}


def test_keyword_recall_uses_authorized_corpus(monkeypatch):
    from app import query
    calls = []
    def authorized(db, tenant, user, kbs):
        calls.append((tenant, user, kbs))
        return [(SimpleNamespace(id='ok', chunk_text='经费预算'), None)]
    monkeypatch.setattr(query, 'authorized_chunks', authorized)
    result = query.keyword_recall(None, 'tenant', 'user', '经费预算如何分配', ['kb'], 10)
    assert calls == [('tenant', 'user', ['kb'])]
    assert result[0][0] == 'ok'

