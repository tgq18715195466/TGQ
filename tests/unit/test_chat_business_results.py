"""Chat facts and checkpoint interrupts, with no live model or ERP calls."""
from copy import deepcopy
import json
from types import SimpleNamespace

from fastapi import HTTPException
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
import pytest

from agent.business_results import normalize_business_result
from agent.schema import Message
from agent.procurement_workflow import supplement_form
from api_view.api import chat, history
from api_view.interrupts import format_interrupts, validate_resume


class Loader:
    def __init__(self, chunks=(), pending=(), saved=()):
        self.chunks = chunks
        self.pending = list(pending)
        self.saved = deepcopy(list(saved))
        self.agent = self
        self.stream_options = None
        self.input = None

    def create_config(self, *args, **kwargs):
        return {}

    async def get_display_messages(self, *args):
        return deepcopy(self.saved)

    async def save_display_messages(self, key, messages):
        self.saved = deepcopy(messages)
        return True

    async def get_pending_interrupts(self, *args):
        return self.pending

    async def get_current_messages(self, *args):
        return []

    async def astream(self, **kwargs):
        self.stream_options = kwargs['stream_mode']
        self.input = kwargs['input']
        for chunk in self.chunks:
            yield chunk


def msg(token, ns=()):
    return {'type': 'messages', 'ns': ns, 'data': (token, {})}


def tool(name, raw, call_id='call-1', ns=(), status='success'):
    return msg(ToolMessage(name=name, content=json.dumps(raw, ensure_ascii=False), tool_call_id=call_id, status=status), ns)


def summary(text):
    args = json.dumps({'answer': text}, ensure_ascii=False)
    return [msg(AIMessageChunk(content='', tool_call_chunks=[{'name': 'publish_business_answer', 'id': 'answer-call', 'index': 0, 'args': args}])),
            {'type': 'values', 'ns': (), 'data': {'messages': [ToolMessage(name='publish_business_answer', content=text, tool_call_id='answer-call')]}}]


async def run(monkeypatch, chunks, pending=(), saved=(), resume=None):
    loader = Loader(chunks, pending, saved)
    monkeypatch.setattr(chat, 'agent_loader', loader)
    events = [json.loads(raw[6:]) async for raw in chat.stream_chat_response(message='查询', thread_id='facts', resume_data=resume)]
    assert not any(e['type'] == 'error' for e in events), events
    return events, loader


@pytest.mark.parametrize('ns', [(), ('tools:child',)])
async def test_real_query_fact_event_and_assistant_share_id(monkeypatch, ns):
    raw = {'ok': True, 'data': {'records': [{'id': 77, 'name': '真实物料', 'purchasePrice': '0.10', 'status': 1, 'request_id': 'secret'}], 'total': 9, 'current': 1, 'size': 1}}
    raw['business'] = normalize_business_result('part_query', raw).model_dump(mode='json')
    chunks = [tool('part_query', raw, ns=ns)] + summary('建议联系供应商确认交期。')
    events, loader = await run(monkeypatch, chunks)
    fact = next(e for e in events if e['type'] == 'business_result')
    answer = next(e for e in events if e['type'] == 'answer')
    assistant = next(m for m in loader.saved if m['role'] == 'assistant')
    assert fact['id'] == answer['id'] == assistant['id']
    assert fact['result']['result_id'] == 'call-1'
    assert assistant['business_results'] == [fact['result']]
    assert fact['result']['records'] == [{'name': '真实物料', 'purchase_price': '0.10'}]
    assert fact['result']['total'] == 9 and fact['result']['returned_count'] == 1
    assert fact['result']['complete'] is False
    assert 'secret' not in json.dumps(fact, ensure_ascii=False)
    assert 'source_tool' not in fact['result']


async def test_fact_upsert_uses_tool_call_id_and_keeps_one_assistant(monkeypatch):
    raw = {'ok': True, 'data': [{'name': '供应商甲', 'status': 1}]}
    events, loader = await run(monkeypatch, [tool('supplier_query', raw), tool('supplier_query', raw), tool('supplier_query', raw, 'call-2')] + summary('建议核对联系方式。'))
    assistants = [m for m in loader.saved if m['role'] == 'assistant']
    assert len(assistants) == 1
    assert [r['result_id'] for r in assistants[0]['business_results']] == ['call-1']
    assert len([e for e in events if e['type'] == 'business_result']) == 1


async def test_resume_upserts_interrupted_assistant_and_history_round_trips(monkeypatch):
    events, loader = await run(monkeypatch, [tool('supplier_query', {'ok': True, 'data': []})], pending=[{'type': 'interrupt', 'interrupt_id': 'i', 'interrupt_type': 'hitl_approval'}])
    assistant = next(m for m in loader.saved if m['role'] == 'assistant')
    assert assistant['content'] == '' and len(assistant['business_results']) == 1
    previous = deepcopy(loader.saved)
    events, loader = await run(monkeypatch, [tool('supplier_query', {'ok': True, 'data': [{'name': '更新后的供应商'}]})] + summary('建议确认交期。'), saved=previous, resume={'i': {'decisions': [{'type': 'approve'}]}})
    assistants = [m for m in loader.saved if m['role'] == 'assistant']
    assert len(assistants) == 1 and assistants[0]['id'] == assistant['id']
    assert len(assistants[0]['business_results']) == 1
    monkeypatch.setattr(history, 'agent_loader', loader)
    restored = await history.get_session_messages('facts', x_user_id='anonymous')
    assert restored.messages[-1].business_results == assistants[0]['business_results']
    state = await chat.get_chat_state('facts', x_user_id='anonymous')
    assert next(m for m in state.messages if m.role == 'assistant').business_results == assistants[0]['business_results']


@pytest.mark.parametrize('raw,outcome', [
    ({'ok': False, 'error': {'code': 'SUPPLIER_NOT_ACTIVE'}}, 'failure'),
    ({'ok': True, 'data': {'orderNumber': 'PO1', 'status': 1}}, 'unknown'),
    ({'ok': False, 'outcome': 'unknown', 'error': {'code': 'EXECUTION_UNKNOWN'}}, 'unknown'),
    ({'ok': True, 'verified': True, 'data': {'orderNumber': 'PO1', 'status': 1}}, 'unknown'),
    ({'ok': True, 'verificationOutcome': 'success', 'data': {'orderNumber': 'PO1', 'status': 1}}, 'unknown'),
])
async def test_unconfirmed_write_suppresses_late_model_success(monkeypatch, raw, outcome):
    events, loader = await run(monkeypatch, [tool('order_create', raw)] + summary('采购订单已创建成功。'))
    fact = next(e for e in events if e['type'] == 'business_result')
    assert fact['result']['execution']['outcome'] == outcome
    assert not any('创建成功' in e.get('content', '') for e in events)
    answer = next(e for e in events if e['type'] == 'answer')
    assert answer['content'] == fact['result']['execution']['message']
    assert not any(e['type'] == 'answer_delta' for e in events)


@pytest.mark.parametrize('outcome,code', [('cancelled', 'CANCELLED'), ('needs_information', 'MISSING_INFORMATION')])
async def test_neutral_workflow_outcomes_are_not_error_boxes(monkeypatch, outcome, code):
    events, loader = await run(monkeypatch, [tool('purchase_request_create', {'ok': False, 'outcome': outcome, 'error': {'code': code}})] + summary('采购申请已成功提交。'))
    assert not any(e['type'] in {'error', 'tool_error', 'answer_delta'} for e in events)
    fact = next(e for e in events if e['type'] == 'business_result')
    assert fact['result']['execution']['outcome'] == outcome
    assert all(m.get('tool_status') != 'error' for m in loader.saved)
    assert '成功提交' not in events[-1]['content']


async def test_verified_write_allows_summary_but_removes_markdown_tables(monkeypatch):
    raw = {'ok': True, 'verified': True, 'verificationOutcome': 'success', 'outcome': 'success', 'data': {'orderNumber': 'PO2', 'status': 1}}
    text = '采购结果已核验。\n\n| 订单编号 | 状态 |\n| --- | --- |\n| MODEL-FAKE | 已完成 |\n\n建议联系供应商确认交期。'
    events, loader = await run(monkeypatch, [tool('order_create', raw)] + summary(text))
    fact = next(e for e in events if e['type'] == 'business_result')
    assert fact['result']['execution']['outcome'] == 'success'
    assert fact['result']['records'][0]['status_name'] == '待审核'
    assert all('MODEL-FAKE' not in e.get('content', '') for e in events)
    assert '建议联系供应商确认交期。' in events[-1]['content']


async def test_custom_procurement_progress_uses_fixed_chinese_stage(monkeypatch):
    chunks = [{'type': 'custom', 'ns': ('tools:nested',), 'data': {'type': 'procurement_progress', 'stage': 'verifying', 'message': 'Injected arbitrary text'}},
              {'type': 'custom', 'ns': (), 'data': {'type': 'other', 'message': 'private'}}]
    events, loader = await run(monkeypatch, chunks)
    assert loader.stream_options == ['messages', 'values', 'custom']
    progress = next(e for e in events if e['type'] == 'procurement_progress')
    assert progress['message'] == '正在读取业务单据，核验真实提交结果。'
    assert not any('private' in e.get('message', '') for e in events)


async def test_mcp_text_blocks_are_decoded_and_argument_facts_are_not_used(monkeypatch):
    call = msg(AIMessageChunk(content='', tool_call_chunks=[{'name': 'part_query', 'id': 'call-1', 'index': 0, 'args': '{"name":"模型虚构","purchasePrice":999}'}]))
    result = msg(ToolMessage(name='part_query', content=[{'type': 'text', 'text': '{"ok":true,"data":{"records":[{"name":"真实"}],"total":1}}'}], tool_call_id='call-1'))
    events, loader = await run(monkeypatch, [call, result])
    fact = next(e for e in events if e['type'] == 'business_result')
    assert fact['result']['records'] == [{'name': '真实'}]
    assert len([m for m in loader.saved if m['role'] == 'assistant']) == 1


async def test_tool_error_status_overrides_success_envelope(monkeypatch):
    events, _ = await run(monkeypatch, [tool('supplier_query', {'ok': True, 'data': [{'name': '甲'}]}, status='error')] + summary('操作成功。'))
    fact = next(e for e in events if e['type'] == 'business_result')
    assert fact['result']['execution']['outcome'] != 'success'
    assert not any('操作成功' in e.get('content', '') for e in events)


def test_message_schema_preserves_business_results():
    result = {'title': '物料查询', 'result_id': 'call-1', 'records': []}
    assert Message(id='a', role='assistant', business_results=[result]).model_dump()['business_results'] == [result]


def procurement_info():
    value = supplement_form('purchase_request_create', {'part_id': 7, 'quantity': None, 'reason': None}, ['数量', '原因'])
    return format_interrupts([SimpleNamespace(id='info', value=value)], 'facts')[0]


def test_checkpoint_procurement_fields_and_reason_are_preserved():
    item = procurement_info()
    assert item['workflow_type'] == 'procurement'
    assert item['workflow_action'] == 'purchase_request_create'
    assert any(f['path'] == '0.reason' for f in item['fields'])
    response = validate_resume([item], {'interrupt_id': 'info', 'values': {'0.orderDetail.0.quantity': '8', '0.reason': '用户补货需求'}, 'supplement': '备注'})
    assert response['info']['values'] == {'0.orderDetail.0.quantity': 8, '0.reason': '用户补货需求'}
    assert response['info']['supplement'] == '备注'


@pytest.mark.parametrize('response', [{'supplement': '数量8'}, {'values': {'0.fake': 8}}, {'values': {'0.orderDetail.0.quantity': '8.5'}}, {'values': {'0.reason': ''}}])
def test_procurement_supplement_rejects_legacy_or_invalid_values(response):
    with pytest.raises(HTTPException) as exc:
        validate_resume([procurement_info()], {'responses': {'info': response}})
    assert exc.value.status_code == 422


@pytest.mark.parametrize('action', ['retry', 'cancel'])
def test_recovery_interrupt_requires_checkpoint_and_normalizes_action(action):
    item = format_interrupts([SimpleNamespace(id='recovery', value={'type': 'workflow_recovery', 'workflow_type': 'procurement', 'write_attempted': True, 'message': '请核验结果。'})], 'facts')[0]
    assert item['interrupt_type'] == 'workflow_recovery'
    assert item['workflow_type'] == 'procurement'
    assert item['write_attempted'] is True
    assert validate_resume([item], {'interrupt_id': 'recovery', 'workflow_action': action}) == {'recovery': {'workflow_action': action}}


@pytest.mark.parametrize('action', ['approve', '', None])
def test_recovery_rejects_arbitrary_actions(action):
    item = {'interrupt_id': 'r', 'interrupt_type': 'workflow_recovery', 'workflow_type': 'procurement'}
    with pytest.raises(HTTPException) as exc:
        validate_resume([item], {'interrupt_id': 'r', 'workflow_action': action})
    assert exc.value.status_code == 422


async def test_readonly_retry_upserts_original_fact_and_keeps_summary_out_of_tables(monkeypatch):
    failed = chat.public_business_result('supplier_query', {'ok': False, 'error': {'code': 'ERP_UNAVAILABLE'}}, 'call-1')
    saved = [
        {'id': 'call-1', 'role': 'tool', 'tool_name': 'supplier_query', 'args': '{"supplier_id":7}', 'failure': {'retryable': True}},
        {'id': 'original-answer', 'role': 'assistant', 'content': '先前查询失败。', 'business_results': [failed], 'channel': 'answer'},
    ]
    loader = Loader(saved=saved)
    monkeypatch.setattr(chat, 'agent_loader', loader)
    async def invoke(name, args):
        assert name == 'supplier_query' and args == {'supplier_id': 7}
        return {'ok': True, 'data': [{'name': '真实供应商', 'status': 1}]}
    monkeypatch.setattr(chat, 'invoke_readonly_tool', invoke)
    response = await chat.retry_tool('facts', 'call-1', x_user_id='anonymous')
    assistants = [m for m in loader.saved if m['role'] == 'assistant']
    assert len(assistants) == 1
    assert response['answer']['id'] == 'original-answer'
    assert response['answer']['business_results'][0]['result_id'] == 'call-1'
    assert response['answer']['business_results'][0]['records'] == [{'name': '真实供应商', 'status_name': '合作中'}]
    assert '|' not in response['answer']['content']


async def test_failed_query_blocks_model_success_even_without_normalizable_tool(monkeypatch):
    events, _ = await run(monkeypatch, [tool('generate_visualization', {'ok': False, 'error': {'code': 'TOOL_ERROR'}})] + summary('操作成功。'))
    assert not any('操作成功' in e.get('content', '') for e in events)
    assert not any(e['type'] == 'answer_delta' for e in events)


async def test_parallel_answer_and_write_never_emit_early_success(monkeypatch):
    chunks = [msg(AIMessageChunk(content='', tool_call_chunks=[
        {'name': 'order_create', 'id': 'call-1', 'index': 0, 'args': '{}'},
        {'name': 'publish_business_answer', 'id': 'answer-call', 'index': 1, 'args': '{"answer":"采购订单已创建成功。"}'},
    ])), tool('order_create', {'ok': False, 'error': {'code': 'SUPPLIER_NOT_ACTIVE'}})]
    events, _ = await run(monkeypatch, chunks)
    assert not any('创建成功' in e.get('content', '') for e in events)


def test_approval_preserves_procurement_workflow_type():
    item = format_interrupts([SimpleNamespace(id='approve', value={'workflow_type': 'procurement', 'action_requests': [{'name': 'order_create', 'args': {}}], 'review_configs': []})], 'facts')[0]
    assert item['workflow_type'] == 'procurement'
    assert item['interrupt_type'] == 'hitl_approval'


def test_recovery_stale_id_cannot_resume():
    item = {'interrupt_id': 'r', 'interrupt_type': 'workflow_recovery', 'workflow_type': 'procurement'}
    with pytest.raises(HTTPException) as exc:
        validate_resume([item], {'interrupt_id': 'stale', 'workflow_action': 'retry'})
    assert exc.value.status_code == 409


class ProcurementService:
    """An isolated fake service records calls; it cannot reach business storage."""
    def __init__(self):
        self.calls = []
        self.saved = None
        self.verify_found = True

    async def invoke(self, name, args):
        self.calls.append((name, deepcopy(args)))
        if name == 'part_query':
            return {'ok': True, 'data': {'records': [{'id': 7, 'name': '测试物料', 'supplier_id': 2}], 'total': 1}}
        if name == 'supplier_query':
            return {'ok': True, 'data': [{'id': 2, 'name': '测试供应商', 'status': 1}]}
        if name == 'replenishment_suggestions':
            return {'ok': True, 'data': {'records': [{'id': 7, 'enabled': True}], 'total': 1}}
        if name == 'purchase_request_create':
            self.saved = deepcopy(args)
            return {'ok': True, 'data': {'id': 99, 'status': 'pending'}}
        if name == 'procurement_verification':
            return {'ok': True, 'data': {'found': self.verify_found, 'record': {**self.saved, 'id': 99, 'status': 'pending', 'name': '测试物料', 'supplier_id': 2, 'supplier_name': '测试供应商'}}}
        raise AssertionError(name)


def procurement_harness(monkeypatch, missing=False):
    from datetime import date, timedelta
    from typing import Annotated, TypedDict
    from langchain_core.tools import StructuredTool
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import StateGraph, START, END
    from langgraph.graph.message import add_messages
    from langgraph.prebuilt import ToolNode
    from api_view.agent_loader import AgentLoader
    from agent.tools.procurement import create_procurement_tools

    service = ProcurementService()
    names = ['supplier_query', 'part_query', 'replenishment_suggestions', 'purchase_request_create', 'order_create', 'procurement_verification']
    def operation(name):
        async def call(**args):
            return await service.invoke(name, args)
        return call
    raw_tools = [StructuredTool.from_function(name=name, description=name, coroutine=operation(name), args_schema={'type': 'object', 'properties': {}, 'additionalProperties': True}) for name in names]
    class State(TypedDict):
        messages: Annotated[list, add_messages]
    def plan(state):
        draft = {'part_id': 7, 'quantity': None if missing else 3, 'unit_price': '0.10', 'expected_date': (date.today() + timedelta(days=10)).isoformat(), 'reason': None if missing else '人工补货需求'}
        return {'messages': [AIMessage(content='', tool_calls=[{'name': 'purchase_request_create', 'args': draft, 'id': 'real-procurement-call'}])]}
    builder = StateGraph(State)
    builder.add_node('plan', plan)
    builder.add_node('tools', ToolNode(create_procurement_tools(raw_tools)))
    builder.add_edge(START, 'plan')
    builder.add_edge('plan', 'tools')
    builder.add_edge('tools', END)
    class Repo:
        def __init__(self):
            self.saved = []
        async def get_display_messages(self, key):
            return deepcopy(self.saved)
        async def save_display_messages(self, key, messages):
            self.saved = deepcopy(messages)
            return True
    loader = object.__new__(AgentLoader)
    loader._agent = builder.compile(checkpointer=InMemorySaver())
    loader._persistence = Repo()
    monkeypatch.setattr(chat, 'agent_loader', loader)
    monkeypatch.setattr(history, 'agent_loader', loader)
    return loader, service


async def open_events(resume=None):
    response = await chat._open_stream('nested-facts', 'anonymous', None, message='合成采购测试' if resume is None else None, resume=resume)
    events = [json.loads(raw[6:]) async for raw in response.body_iterator]
    assert not any(e['type'] == 'error' for e in events), events
    return events


@pytest.fixture
def procurement_identity():
    from backend.auth import current_identity
    token = current_identity.set({'id': 7, 'username': 'buyer', 'display_name': '采购员', 'role': 'purchaser', 'enabled': True})
    yield
    current_identity.reset(token)


async def test_real_nested_checkpoint_form_approval_and_fact_history(monkeypatch, procurement_identity):
    loader, service = procurement_harness(monkeypatch, missing=True)
    events = await open_events()
    assert 'procurement_progress' in [e['type'] for e in events]
    pending = next(e for e in events if e['type'] == 'interrupt')
    assert pending['workflow_type'] == 'procurement'
    assert pending['interrupt_type'] == 'order_info_supplement'
    assert service.saved is None
    events = await open_events({'interrupt_id': pending['interrupt_id'], 'values': {'0.orderDetail.0.quantity': '8', '0.reason': '人工确认补货'}})
    pending = next(e for e in events if e['type'] == 'interrupt')
    assert pending['workflow_type'] == 'procurement'
    assert pending['interrupt_type'] == 'hitl_approval'
    assert service.saved is None
    events = await open_events({'interrupt_id': pending['interrupt_id'], 'decisions': [{'type': 'approve'}]})
    fact = next(e for e in events if e['type'] == 'business_result')
    assert fact['result']['execution']['outcome'] == 'success'
    assert fact['result']['records'][0]['quantity'] == 8
    assert fact['result']['records'][0]['status_name'] == '待审批'
    assert sum(name == 'purchase_request_create' for name, _ in service.calls) == 1
    result = await history.get_session_messages('nested-facts', x_user_id='anonymous')
    assistants = [m for m in result.messages if m.role == 'assistant']
    assert len(assistants) == 1
    assert assistants[0].business_results == [fact['result']]


@pytest.mark.parametrize('action', ['retry', 'cancel'])
async def test_real_nested_recovery_resumes_only_verification(monkeypatch, procurement_identity, action):
    loader, service = procurement_harness(monkeypatch)
    service.verify_found = False
    pending = next(e for e in await open_events() if e['type'] == 'interrupt')
    events = await open_events({'interrupt_id': pending['interrupt_id'], 'decisions': [{'type': 'approve'}]})
    pending = next(e for e in events if e['type'] == 'interrupt')
    assert pending['interrupt_type'] == 'workflow_recovery'
    assert pending['workflow_type'] == 'procurement' and pending['write_attempted']
    service.verify_found = True
    events = await open_events({'interrupt_id': pending['interrupt_id'], 'workflow_action': action})
    fact = next(e for e in events if e['type'] == 'business_result')
    assert fact['result']['execution']['outcome'] == ('success' if action == 'retry' else 'unknown')
    assert sum(name == 'purchase_request_create' for name, _ in service.calls) == 1
    assert sum(name == 'procurement_verification' for name, _ in service.calls) == (2 if action == 'retry' else 1)
    assert len([m for m in loader._persistence.saved if m['role'] == 'assistant']) == 1


async def test_real_nested_cancel_is_neutral_and_never_submits(monkeypatch, procurement_identity):
    loader, service = procurement_harness(monkeypatch)
    pending = next(e for e in await open_events() if e['type'] == 'interrupt')
    events = await open_events({'interrupt_id': pending['interrupt_id'], 'decisions': [{'type': 'reject'}]})
    assert not any(e['type'] in {'error', 'tool_error'} for e in events)
    fact = next(e for e in events if e['type'] == 'business_result')
    assert fact['result']['execution']['outcome'] == 'cancelled'
    assert service.saved is None


async def test_query_report_streams_progressively_with_curated_markdown_table(monkeypatch):
    fragments = [
        '{"answer":"已查询到供应商资料。\\n',
        '供应商名称 | 信用等级\\n',
        '--- | ---\\n真实供应商 | A\\n\\n',
        '建议先确认交期。\\n',
        '然后安排采购。"}',
    ]
    chunks = [tool('supplier_query', {'ok': True, 'data': [{'name': '真实供应商', 'status': 1, 'credit_rating': 'A'}]})]
    for index, fragment in enumerate(fragments):
        chunks.append(msg(AIMessageChunk(content='', tool_call_chunks=[{'name': 'publish_business_answer' if index == 0 else None, 'id': 'summary' if index == 0 else None, 'index': 0, 'args': fragment}])))
    text = '已查询到供应商资料。\n供应商名称 | 信用等级\n--- | ---\n真实供应商 | A\n\n建议先确认交期。\n然后安排采购。'
    chunks.extend(summary(text)[1:])
    loader = Loader(chunks)
    monkeypatch.setattr(chat, 'agent_loader', loader)
    stream = chat.stream_chat_response('查询', 'typing')
    early = []
    async for raw in stream:
        event = json.loads(raw[6:])
        early.append(event)
        if event['type'] == 'answer_delta':
            assert loader.saved == []
            assert event['content'] == '已查询到供应商资料。\n'
            assert any(e['type'] == 'business_result' for e in early)
            break
    else:
        pytest.fail('Facts must retain progressive answer SSE before graph completion')
    events = early + [json.loads(raw[6:]) async for raw in stream]
    deltas = [e for e in events if e['type'] == 'answer_delta']
    assert len(deltas) >= 2
    content = ''.join(e['content'] for e in deltas)
    assert content == text
    assert '建议先确认交期。' in content and content.endswith('然后安排采购。')
    assert len({e['id'] for e in events if e['type'] in {'business_result', 'answer_delta', 'answer'}}) == 1


async def test_fact_only_done_has_full_assistant_metadata(monkeypatch):
    events, loader = await run(monkeypatch, [tool('supplier_query', {'ok': True, 'data': []})])
    fact = next(e for e in events if e['type'] == 'business_result')
    done = events[-1]
    assistant = next(m for m in loader.saved if m['role'] == 'assistant')
    assert done['id'] == fact['id'] == assistant['id']
    assert done['business_results'] == assistant['business_results'] == [fact['result']]
    assert done['content'] == assistant['content'] == ''
