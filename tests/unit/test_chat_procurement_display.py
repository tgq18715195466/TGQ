"""Display policy for procurement preflight queries and correlated failures."""
import json
from langchain_core.messages import AIMessageChunk
from test_chat_business_results import run, msg, tool, summary


def call(name, call_id, ns=()):
    return msg(AIMessageChunk(content='', tool_call_chunks=[{'name': name, 'id': call_id, 'index': 0, 'args': '{}'}]), ns)


def queries(ns=()):
    chunks = []
    for name, call_id, raw in [
        ('part_query', 'part-preflight', {'ok': True, 'data': {'records': [{'name': '内部物料'}], 'total': 1}}),
        ('supplier_query', 'supplier-preflight', {'ok': True, 'data': [{'name': '内部供应商', 'status': 1}]}),
        ('replenishment_suggestions', 'stock-preflight', {'ok': True, 'data': {'records': [{'name': '内部补货', 'suggested': 8}], 'total': 1}}),
    ]:
        chunks.extend([call(name, call_id, ns), tool(name, raw, call_id, ns)])
    return chunks


def approved_result():
    return {'ok': True, 'verified': True, 'verificationOutcome': 'success', 'outcome': 'success', 'data': {'quantity': 8, 'unit_price': '2.00', 'status': 'pending'}}


async def test_preflight_queries_never_leak_before_procurement_confirmation(monkeypatch):
    ns = ('tools:procurement-task',)
    pending = [{'type': 'interrupt', 'interrupt_id': 'confirm', 'interrupt_type': 'hitl_approval', 'workflow_type': 'procurement', 'action_requests': [{'name': 'purchase_request_create', 'args': {}}]}]
    events, loader = await run(monkeypatch, queries(ns) + [call('purchase_request_create', 'create', ns)], pending=pending)
    assert not any(e['type'] == 'business_result' for e in events)
    assert events[-1]['business_results'] == []
    assert len([m for m in loader.saved if m['role'] == 'tool']) == 4
    assert not any(m.get('business_results') for m in loader.saved)


async def test_query_then_write_displays_only_actual_write_result(monkeypatch):
    ns = ('tools:procurement-task',)
    chunks = queries(ns) + [call('purchase_request_create', 'create', ns), tool('purchase_request_create', approved_result(), 'create', ns)] + summary('申请已核验，后续仍需审批。')
    events, loader = await run(monkeypatch, chunks)
    assert [e['result']['result_id'] for e in events if e['type'] == 'business_result'] == ['create']
    assert [r['result_id'] for r in events[-1]['business_results']] == ['create']
    assert all(r['result_id'] == 'create' for m in loader.saved for r in m.get('business_results', []))
    assert any(e['type'] == 'answer_delta' for e in events)


async def test_root_preflight_queries_are_also_buffered_until_write_is_known(monkeypatch):
    events, _ = await run(monkeypatch, queries() + [tool('purchase_request_create', approved_result(), 'create')] + summary('申请待审批。'))
    assert [e['result']['result_id'] for e in events if e['type'] == 'business_result'] == ['create']


async def test_pure_readonly_child_query_keeps_every_record_and_progressive_summary(monkeypatch):
    events, loader = await run(monkeypatch, queries(('tools:query-child',)) + summary('查询完成。\n建议核对库存。'))
    results = [e for e in events if e['type'] == 'business_result']
    assert [e['result']['result_id'] for e in results] == ['part-preflight', 'supplier-preflight', 'stock-preflight']
    first_delta = next(i for i, e in enumerate(events) if e['type'] == 'answer_delta')
    assert all(events.index(e) < first_delta for e in results)
    assert len(next(m for m in loader.saved if m['role'] == 'assistant')['business_results']) == 3


async def test_resume_cleans_old_internal_query_cards_with_same_answer_id(monkeypatch):
    events, initial = await run(monkeypatch, queries())
    saved = initial.saved
    assistant = next(m for m in saved if m['role'] == 'assistant')
    saved.insert(-1, {'id': 'create', 'role': 'tool', 'tool_name': 'purchase_request_create', 'args': '{}', 'tool_status': 'calling'})
    events, loader = await run(monkeypatch, [tool('purchase_request_create', approved_result(), 'create')] + summary('申请待审批。'), saved=saved, resume={'confirm': {'decisions': [{'type': 'approve'}]}})
    assert events[-1]['id'] == assistant['id']
    assert [r['result_id'] for r in events[-1]['business_results']] == ['create']
    assert len([m for m in loader.saved if m['role'] == 'assistant']) == 1


async def test_fixed_procurement_progress_hides_preceding_queries(monkeypatch):
    custom = {'type': 'custom', 'ns': ('tools:workflow',), 'data': {'type': 'procurement_progress', 'stage': 'querying'}}
    pending = [{'type': 'interrupt', 'interrupt_id': 'form', 'interrupt_type': 'order_info_supplement', 'workflow_type': 'procurement'}]
    events, _ = await run(monkeypatch, queries() + [custom], pending=pending)
    assert not any(e['type'] == 'business_result' for e in events)
    assert events[-1]['business_results'] == []


async def test_unified_failure_ids_preserve_independent_failures(monkeypatch):
    chunks = [tool('purchase_request_create', {'ok': False, 'error': {'code': 'AUTH_REQUIRED'}}, 'failure-one'),
              tool('inventory_adjust', {'ok': False, 'error': {'code': 'PERMISSION_DENIED'}}, 'failure-two')] + summary('操作成功。')
    events, loader = await run(monkeypatch, chunks)
    assert events[-1]['failure_tool_call_ids'] == ['failure-one', 'failure-two']
    assert len(events[-1]['business_results']) == 2
    assert '操作成功' not in events[-1]['content']
    assistant = next(m for m in loader.saved if m['role'] == 'assistant')
    assert assistant['failure_tool_call_ids'] == ['failure-one', 'failure-two']


async def test_verified_purchase_recovers_internal_query_failure_without_false_failure(monkeypatch):
    chunks = [tool('part_query', {'ok': False, 'error': {'code': 'ERP_UNAVAILABLE'}}, 'query-failed'),
              tool('purchase_request_create', approved_result(), 'created')] + summary('采购申请已提交，等待审批。')
    events, loader = await run(monkeypatch, chunks)
    assert events[-1]['content'] == '采购申请已提交，等待审批。'
    assert any(e['type']=='answer_delta' for e in events)
    assert events[-1]['failure_tool_call_ids'] == ['query-failed']


async def test_task_echo_of_write_failure_does_not_repeat_its_reason(monkeypatch):
    rejected = {'ok': False, 'outcome': 'failure', 'error': {'code': 'PENDING_PURCHASE_REQUEST'}}
    chunks = [tool('purchase_request_create', rejected, 'failed-write'),
              tool('task', rejected, 'delegated-result')] + summary('操作成功。')
    events, _ = await run(monkeypatch, chunks)
    assert events[-1]['content'].count('已有待审批申请') == 1
    assert '操作成功' not in events[-1]['content']
    assert set(events[-1]['failure_tool_call_ids']) == {'failed-write', 'delegated-result'}
