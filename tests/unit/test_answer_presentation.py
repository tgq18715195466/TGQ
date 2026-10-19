"""Deliver a curated report, retaining raw tools as optional evidence."""
from types import SimpleNamespace
import pytest
from langchain_core.messages import HumanMessage, ToolMessage

from test_chat_business_results import run, tool, summary


REPORT = ('已核验 2 条低库存物料，其中 1 条供应商合作中。\n\n'
          '| 物料编码 | 名称 | 当前库存 | 缺口 |\n| --- | --- | --- | --- |\n'
          '| FC3006 | 侧支架 | 3 | 24 |\n\n'
          '![缺口对比](/api/artifacts/test.svg)\n\n优先处理侧支架。')


async def test_query_report_keeps_its_table_chart_and_streamed_text(monkeypatch):
    raw = {'ok': True, 'data': {'records': [{'part_code': 'FC3006', 'name': '侧支架', 'quantity': 3}], 'total': 1}}
    events, loader = await run(monkeypatch, [tool('inventory_warning', raw)] + summary(REPORT))
    assert events[-1]['content'] == REPORT
    assert ''.join(e['content'] for e in events if e['type'] == 'answer_delta') == REPORT
    assert next(m for m in loader.saved if m['role'] == 'assistant')['content'] == REPORT
    assert events[-1]['business_results']  # Evidence survives for inspection.


def test_informational_purchasable_request_is_not_a_purchase_command():
    from agent.request_scope import request_scope
    scope = request_scope('我当前低库存且可采购的物料')
    assert scope.actions == frozenset()
    assert not scope.allows('purchase_request_create')


@pytest.mark.parametrize('user_text, action, tool_name', [
    ('修改可采购物料的预警值为 20', 'master', 'master_change_request'),
    ('取消可采购物料的订单 PO123', 'order_update', 'order_update'),
    ('给这些可采购物料调整库存', 'stock', 'inventory_adjust'),
    ('采购这些可采购物料', 'purchase', 'purchase_request_create'),
])
def test_eligibility_adjective_does_not_override_an_explicit_operation(user_text, action, tool_name):
    from agent.request_scope import request_scope
    scope = request_scope(user_text)
    assert scope.actions == frozenset({action})
    assert scope.allows(tool_name)


def publish(messages, **args):
    from agent.tools.business_answer import publish_business_answer
    return publish_business_answer.func(runtime=SimpleNamespace(state={'messages': messages}), **args)


def test_publish_can_reference_this_turn_successful_task_report_without_retyping():
    messages = [HumanMessage(content='低库存可采购物料'),
                ToolMessage(name='task', tool_call_id='report', content=REPORT)]
    assert publish(messages, source_task_id='report') == REPORT


def test_wrong_or_previous_answer_call_id_uses_the_only_current_task_report():
    messages = [HumanMessage(content='上轮查询'),
                ToolMessage(name='publish_business_answer', tool_call_id='old-answer', content='旧答案'),
                HumanMessage(content='有哪些物料需要购买'),
                ToolMessage(name='part_query', tool_call_id='raw', content='{"ok": true}'),
                ToolMessage(name='task', tool_call_id='current-report', content=REPORT)]
    assert publish(messages, source_task_id='old-answer') == REPORT
    assert publish(messages, source_task_id='nonsense-id') == REPORT


def test_wrong_reference_does_not_guess_between_multiple_current_reports():
    messages = [HumanMessage(content='比较两份报告'),
                ToolMessage(name='task', tool_call_id='first', content='报告一'),
                ToolMessage(name='task', tool_call_id='second', content='报告二')]
    with pytest.raises(ValueError):
        publish(messages, source_task_id='wrong')


def test_reference_to_current_failed_or_non_task_tool_is_not_silently_replaced():
    for current in [ToolMessage(name='task', tool_call_id='wrong', content='失败报告', status='error'),
                    ToolMessage(name='part_query', tool_call_id='wrong', content='原始查询')]:
        messages = [HumanMessage(content='查询'), ToolMessage(name='task', tool_call_id='good', content=REPORT), current]
        with pytest.raises(ValueError):
            publish(messages, source_task_id='wrong')


def test_ambiguous_reference_explains_answer_failure_without_claiming_business_failure():
    from agent.tools.business_answer import AnswerReferenceError
    from api_view.output_protocol import run_failure

    message = run_failure(AnswerReferenceError('internal opaque call ID'))
    assert '报告引用失效' in message
    assert '正式答复尚未发布' in message
    assert '本次处理未完成' not in message
    assert 'opaque' not in message


@pytest.mark.parametrize('messages', [
    [ToolMessage(name='task', tool_call_id='report', content=REPORT), HumanMessage(content='新问题')],
    [HumanMessage(content='查询'), ToolMessage(name='part_query', tool_call_id='report', content=REPORT)],
    [HumanMessage(content='查询'), ToolMessage(name='task', tool_call_id='report', content=REPORT, status='error')],
    [HumanMessage(content='查询'), ToolMessage(name='task', tool_call_id='report', content='{"ok": false, "error": {"code":"PERMISSION_DENIED"}}')],
])
def test_publish_rejects_stale_failed_or_raw_tool_sources(messages):
    with pytest.raises(ValueError):
        publish(messages, source_task_id='report')


def test_report_reference_cannot_append_a_second_duplicate_answer():
    with pytest.raises(ValueError):
        publish([HumanMessage(content='查询'), ToolMessage(name='task', tool_call_id='report', content=REPORT)],
                source_task_id='report', answer='重复总结')


@pytest.mark.parametrize('selected_id', ['report', 'previous-answer'])
async def test_real_graph_stream_publishes_and_persists_referenced_task_report(monkeypatch, selected_id):
    import json
    from langchain.agents import create_agent
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.tools import tool as graph_tool
    from langgraph.checkpoint.memory import InMemorySaver
    from agent.tools.business_answer import publish_business_answer
    from agent.schema import ProcurementContext
    from api_view.agent_loader import AgentLoader
    from api_view.api import chat

    class ReportModel(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    @graph_tool
    def task():
        """Return a prepared analysis report for this offline integration test."""
        return REPORT

    class Repo:
        saved = []
        async def get_display_messages(self, key): return self.saved
        async def save_display_messages(self, key, messages):
            self.saved = list(messages)
            return True

    model = ReportModel(responses=[
        AIMessage(content='', tool_calls=[{'name':'task', 'id':'report', 'args':{}}]),
        AIMessage(content='', tool_calls=[{'name':'publish_business_answer', 'id':'publish', 'args':{'source_task_id':selected_id}}]),
    ])
    loader = object.__new__(AgentLoader)
    loader._agent = create_agent(model, tools=[task, publish_business_answer], context_schema=ProcurementContext, checkpointer=InMemorySaver())
    loader._persistence = Repo()
    monkeypatch.setattr(chat, 'agent_loader', loader)
    events = [json.loads(raw[6:]) async for raw in chat.stream_chat_response('低库存可采购物料', f'report-reference-{selected_id}')]
    assert not any(event['type'] == 'error' for event in events), events
    assert [event['content'] for event in events if event['type'] == 'answer'] == [REPORT]
    assert events[-1]['content'] == REPORT
    assert [message['content'] for message in loader._persistence.saved if message['role'] == 'assistant'] == [REPORT]
