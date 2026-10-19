"""Offline graph test: form submission cannot bypass the next approval."""
import json
import pytest
from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage, HumanMessage
from langchain_core.outputs import ChatResult, ChatGeneration
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from agent.middlewares.order_preflight import OrderPreflightMiddleware
from api_view.interrupts import format_interrupts, validate_resume


class FormModel(BaseChatModel):
    @property
    def _llm_type(self):
        return 'offline-structured-form'

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        results = [m for m in messages if isinstance(m, ToolMessage)]
        if not results:
            calls = [{'id': f'initial-{i}', 'name': 'order_create', 'args': {}} for i in range(2)]
            message = AIMessage(content='', tool_calls=calls)
        elif '用户补充：' in results[-1].content:
            supplement = json.loads(results[-1].content.split('用户补充：', 1)[1])['supplement']
            orders = json.loads(supplement)['orders']
            message = AIMessage(content='', tool_calls=[{'id': f'complete-{i}', 'name': 'order_create', 'args': order} for i, order in enumerate(orders)])
        else:
            message = AIMessage(content='done')
        return ChatResult(generations=[ChatGeneration(message=message)])


@pytest.mark.asyncio
@pytest.mark.parametrize('decision', ['approve', 'reject'])
async def test_structured_resume_replans_two_orders_then_requires_approval(decision):
    writes = []

    @tool
    def order_create(supplierId: int, expectedDeliveryDate: str, orderDetail: list[dict]):
        """Synthetic memory sink; never a real business write."""
        writes.append(supplierId)
        return 'synthetic'

    graph = create_agent(FormModel(), tools=[order_create], middleware=[OrderPreflightMiddleware(
        interrupt_on={'order_create': {'allowed_decisions': ['approve', 'reject']}})], checkpointer=InMemorySaver())
    config = {'configurable': {'thread_id': f'forms-{decision}'}}
    result = await graph.ainvoke({'messages': [HumanMessage(content='补充两份订单')]}, config)
    pending = format_interrupts(result['__interrupt__'], config['configurable']['thread_id'])
    iid = pending[0]['interrupt_id']
    data = {}
    for i in range(2):
        data.update({f'{i}.supplierId': str(15 + i), f'{i}.expectedDeliveryDate': '2099-01-01',
                     f'{i}.orderDetail.0.partId': str(i + 1), f'{i}.orderDetail.0.quantity': '2', f'{i}.orderDetail.0.unitPrice': '10.50'})
    resume = validate_resume(pending, {'responses': {iid: {'values': data}}})
    result = await graph.ainvoke(Command(resume=resume), config)
    assert writes == []
    approvals = format_interrupts(result['__interrupt__'], config['configurable']['thread_id'])
    assert approvals[0]['interrupt_type'] == 'hitl_approval'
    assert [a['args']['supplierId'] for a in approvals[0]['action_requests']] == [15, 16]
    resume = validate_resume(approvals, {'interrupt_id': approvals[0]['interrupt_id'], 'decisions': [{'type': decision}] * 2})
    await graph.ainvoke(Command(resume=resume), config)
    assert writes == ([15, 16] if decision == 'approve' else [])
