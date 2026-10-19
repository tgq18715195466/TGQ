"""Current user goals must bound tools and public evidence, across domains."""
import json
from types import SimpleNamespace

import pytest
from backend.auth import current_identity
from test_tool_permissions import principal, call_request, model_request, named
from test_chat_business_results import Loader, tool, summary
from test_chat_procurement_display import queries
from api_view.api import chat
from agent.middlewares.tool_permissions import RoleToolPermissionsMiddleware


async def stream(monkeypatch, message, chunks, role='purchaser', saved=(), resume=None, agent=None):
    loader = Loader(chunks, saved=saved)
    if agent is not None:
        loader.agent = agent
    monkeypatch.setattr(chat, 'agent_loader', loader)
    token = current_identity.set(principal(role))
    try:
        events = [json.loads(raw[6:]) async for raw in chat.stream_chat_response(
            message=message, thread_id='scope', resume_data=resume)]
    finally:
        current_identity.reset(token)
    assert not any(e['type'] == 'error' for e in events), events
    return events, loader


async def test_purchase_blocked_before_write_does_not_dump_preflight(monkeypatch):
    events, loader = await stream(monkeypatch, '给我采购离合器片', queries() + summary('供应商已终止合作，暂时无法采购。'))
    assert events[-1]['business_results'] == []
    assert not any(e['type'] == 'business_result' for e in events)
    assert events[-1]['content'] == '供应商已终止合作，暂时无法采购。'
    assert not any(m.get('business_results') for m in loader.saved)


async def test_identical_queries_deduplicate_but_different_pages_survive(monkeypatch):
    raw = {'ok': True, 'data': {'records': [{'part_code': 'PC2010', 'name': '离合器片'}], 'total': 2, 'current': 1, 'size': 1}}
    other = {'ok': True, 'data': {'records': [{'part_code': 'PC2011', 'name': '另一物料'}], 'total': 2, 'current': 2, 'size': 1}}
    chunks = [tool('part_query', raw, 'a'), tool('part_query', raw, 'b'), tool('part_query', other, 'c')]
    events, _ = await stream(monkeypatch, '查询所有物料', chunks + summary('已查询。'))
    assert len(events[-1]['business_results']) == 2
    assert len([e for e in events if e['type'] == 'business_result']) == 2


@pytest.mark.parametrize('message,role', [
    ('发起 SUP00027 的恢复合作变更审批', 'purchaser'),
    ('将供应商 SUP00027 暂停合作', 'warehouse'),
    ('修改物料 PC2010 的采购价', 'purchaser'),
    ('审批采购申请', 'purchaser'),
])
async def test_denied_current_goal_never_runs_substitute_purchase(monkeypatch, message, role):
    from langchain.agents import create_agent
    from langchain_core.language_models.fake_chat_models import FakeListChatModel
    from langgraph.checkpoint.memory import InMemorySaver
    from agent.schema import ProcurementContext
    model = FakeListChatModel(responses=['错误：不应调用模型'])
    graph = create_agent(model, middleware=[RoleToolPermissionsMiddleware()], context_schema=ProcurementContext,
                         checkpointer=InMemorySaver()).with_config({'configurable': {'thread_id': 'denial'}})
    old = tool('purchase_request_create', {'ok': False, 'error': {'code': 'SUPPLIER_NOT_ACTIVE', 'retryable': False}})
    events, loader = await stream(monkeypatch, message, [old] + summary('采购失败'), role, agent=graph)
    assert events[-1]['content'] == '当前账号无执行权限，请联系审批人执行。'
    assert events[-1]['business_results'] == []
    assert loader.input is None
    state = await graph.aget_state({'configurable': {'thread_id': 'denial'}})
    assert [m.content for m in state.values['messages']] == [message, events[-1]['content']]


@pytest.mark.parametrize('message,wrong_tool', [
    ('发起 SUP00027 的恢复合作变更审批', 'purchase_request_create'),
    ('给我采购离合器片', 'master_change_request'),
    ('查询供应商状态', 'order_create'),
    ('盘亏 PC2010 两件', 'order_update'),
])
async def test_action_guard_rejects_wrong_domain_even_for_admin(message, wrong_tool):
    request = call_request(wrong_tool)
    request.runtime.context = SimpleNamespace(request_text=message)
    mark = current_identity.set(principal('admin'))
    try:
        result = await RoleToolPermissionsMiddleware().awrap_tool_call(request, lambda r: pytest.fail('wrong operation executed'))
    finally:
        current_identity.reset(mark)
    assert json.loads(result.content)['error']['code'] == 'REQUEST_ACTION_MISMATCH'


def test_child_model_receives_goal_and_explicit_permission_notice():
    request = model_request([named('master_change_request'), named('purchase_request_create'), named('supplier_query')])
    request.runtime.context = SimpleNamespace(request_text='发起 SUP00027 的恢复合作变更审批')
    mark = current_identity.set(principal('purchaser'))
    try:
        captured = RoleToolPermissionsMiddleware().wrap_model_call(request, lambda r: r)
    finally:
        current_identity.reset(mark)
    assert [t.name for t in captured.tools] == ['supplier_query']
    assert '主数据' in captured.system_message.content and '审批人' in captured.system_message.content
    assert '发起 SUP00027 的恢复合作变更审批' in captured.system_message.content


async def test_readonly_request_only_shows_requested_domain(monkeypatch):
    events, _ = await stream(monkeypatch, '查询供应商状态', queries() + summary('已查询。'))
    assert [r['kind'] for r in events[-1]['business_results']] == ['supplier']


@pytest.mark.parametrize('text,allowed,blocked', [
    ('查询供应商，然后采购离合器片', 'purchase_request_create', 'inventory_adjust'),
    ('恢复合作，然后采购离合器片', 'master_change_request', 'inventory_adjust'),
    ('不要采购，只查看物料', 'part_query', 'purchase_request_create'),
    ('如何恢复合作', 'supplier_query', 'master_change_request'),
    ('修改采购订单的供应商', 'order_update', 'master_change_request'),
    ('开始采购离合器片', 'purchase_request_create', 'order_update'),
    ('开始采购订单 PO123', 'order_update', 'purchase_request_create'),
    ('查询供应商并发起恢复合作变更审批', 'master_change_request', 'purchase_request_create'),
    ('帮我创建一张待审批的采购申请', 'purchase_request_create', 'order_update'),
    ('提交采购申请审批', 'purchase_request_create', 'order_update'),
    ('帮我采购离合器片不要查询库存', 'purchase_request_create', 'inventory_adjust'),
])
def test_scope_distinguishes_mixed_readonly_and_domain_actions(text, allowed, blocked):
    from agent.request_scope import request_scope
    scope = request_scope(text)
    assert scope.allows(allowed)
    assert not scope.allows(blocked)


def test_negated_query_is_not_a_requested_deliverable():
    from agent.request_scope import request_scope
    scope = request_scope('帮我向供应商购买10件物料，无需查询库存')
    assert scope.allows('purchase_request_create')
    assert scope.query_kinds is None


async def test_explicit_query_survives_mixed_write_request(monkeypatch):
    from test_chat_procurement_display import approved_result
    chunks = queries() + [tool('purchase_request_create', approved_result(), 'created')] + summary('采购申请待审批。')
    events, _ = await stream(monkeypatch, '查询供应商，然后采购离合器片', chunks)
    assert [r['kind'] for r in events[-1]['business_results']] == ['supplier', 'purchase_request']


async def test_resume_uses_original_user_request_not_confirmation_text(monkeypatch):
    saved = [{'id': 'u', 'role': 'user', 'content': '给我采购离合器片'},
             {'id': 'p', 'role': 'tool', 'tool_name': 'purchase_request_create', 'args': '{}', 'tool_status': 'calling'}]
    events, loader = await stream(monkeypatch, None, queries() + summary('等待资料。'), saved=saved,
                                  resume={'i': {'decisions': [{'type': 'approve'}]}})
    assert events[-1]['business_results'] == []


async def test_new_turn_does_not_reuse_old_purchase_scope(monkeypatch):
    saved = [{'id': 'u', 'role': 'user', 'content': '给我采购离合器片'},
             {'id': 'a', 'role': 'assistant', 'content': '采购受阻。'}]
    events, _ = await stream(monkeypatch, '查询供应商状态', queries() + summary('已查询。'), saved=saved)
    assert [r['kind'] for r in events[-1]['business_results']] == ['supplier']


async def test_wrong_call_id_does_not_steal_another_same_named_call(monkeypatch):
    from test_chat_procurement_display import call
    chunks = [call('part_query', 'active'), tool('part_query', {'ok': True, 'data': []}, 'unrelated'),
              tool('part_query', {'ok': True, 'data': [{'name': '当前物料'}]}, 'active')]
    events, _ = await stream(monkeypatch, '查询物料', chunks + summary('完成。'))
    assert [r['result_id'] for r in events[-1]['business_results']] == ['unrelated', 'active']


def test_wrong_operation_never_opens_confirmation(monkeypatch):
    from agent.subagents.loader import _remaining_order_preflight
    from langchain_core.messages import AIMessage
    import langchain.agents.middleware.human_in_the_loop as hitl
    monkeypatch.setattr(hitl, 'interrupt', lambda _: pytest.fail('wrong confirmation displayed'))
    gate = _remaining_order_preflight({'order_update': {'allowed_decisions': ['approve', 'reject']}})
    runtime = SimpleNamespace(context=SimpleNamespace(request_text='恢复供应商合作状态'))
    mark = current_identity.set(principal('admin'))
    try:
        result = gate.after_model({'messages': [AIMessage(content='', tool_calls=[
            {'name': 'order_update', 'id': 'wrong', 'args': {'order_id': 1}}])]}, runtime)
    finally:
        current_identity.reset(mark)
    assert result['jump_to'] == 'model'
    assert json.loads(result['messages'][0].content)['error']['code'] == 'REQUEST_ACTION_MISMATCH'


async def test_real_deepagents_child_inherits_original_goal_despite_bad_delegation():
    from deepagents import create_deep_agent
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.messages import AIMessage, ToolMessage
    from langchain_core.tools import StructuredTool
    from agent.schema import ProcurementContext

    class ScriptedModel(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self  # Deliberately emulate a model choosing a filtered tool.

    executed = []
    purchase = StructuredTool.from_function(lambda: executed.append(True) or 'wrong',
                                           name='purchase_request_create', description='Test write')
    child = ScriptedModel(responses=[AIMessage(content='', tool_calls=[
        {'name': 'purchase_request_create', 'id': 'bad-write', 'args': {}}]), AIMessage(content='已阻止错误操作。')])
    parent = ScriptedModel(responses=[AIMessage(content='', tool_calls=[
        {'name': 'task', 'id': 'task-1', 'args': {'subagent_type': 'worker', 'description': '继续之前的采购申请'}}]),
        AIMessage(content='已停止。')])
    graph = create_deep_agent(model=parent, middleware=[RoleToolPermissionsMiddleware()],
        context_schema=ProcurementContext, subagents=[{
            'name': 'worker', 'description': 'Test worker', 'system_prompt': 'Test', 'model': child,
            'tools': [purchase], 'middleware': [RoleToolPermissionsMiddleware()],
        }])
    mark = current_identity.set(principal('admin'))
    try:
        chunks = [chunk async for chunk in graph.astream(
            {'messages': [{'role': 'user', 'content': '恢复合作'}]},
            context=ProcurementContext('1', 'admin', '恢复合作'),
            stream_mode=['messages', 'values'], subgraphs=True, version='v2')]
    finally:
        current_identity.reset(mark)
    assert executed == []
    errors = [chunk['data'][0] for chunk in chunks if chunk['type'] == 'messages'
              and isinstance(chunk['data'][0], ToolMessage) and chunk['data'][0].name == 'purchase_request_create']
    assert errors and 'REQUEST_ACTION_MISMATCH' in errors[0].content


async def test_corrected_operation_is_not_overwritten_by_rejected_wrong_attempt(monkeypatch):
    wrong = {'ok': False, 'error': {'code': 'REQUEST_ACTION_MISMATCH', 'retryable': False}}
    correct = {'ok': True, 'verified': True, 'outcome': 'success', 'data': {'status': 'pending'}}
    chunks = [tool('purchase_request_create', wrong, 'wrong', status='error'),
              tool('master_change_request', correct, 'correct')] + summary('变更申请已提交。')
    events, _ = await stream(monkeypatch, '发起供应商恢复合作', chunks, role='admin')
    assert events[-1]['content'] == '变更申请已提交。'
    assert len(events[-1]['business_results']) == 1
