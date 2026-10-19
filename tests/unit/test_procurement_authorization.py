"""Procurement forms and checkpoint recovery must use the authenticated role."""
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from backend.auth import current_identity
from agent.tools import hitl_tools
from agent.tools.auth_bridge import tool_allowed_for_identity
from api_view.api import chat
from agent.subagents.loader import _remaining_order_preflight
from langchain_core.messages import AIMessage


@pytest.mark.parametrize('role,allowed', [('warehouse', False), ('purchaser', True), ('approver', True), ('admin', True), (None, False)])
def test_procurement_supplement_tool_requires_purchase_permission(role, allowed):
    assert tool_allowed_for_identity('request_order_info', {'role': role}) is allowed


@pytest.mark.parametrize('role', ['warehouse', None])
def test_direct_tool_call_cannot_open_unauthorized_purchase_form(monkeypatch, role):
    monkeypatch.setattr(hitl_tools, 'interrupt', lambda _: pytest.fail('unauthorized interrupt opened'))
    token = current_identity.set({'role': role} if role else None)
    try:
        result = json.loads(hitl_tools.request_order_info.invoke({'missing_fields': '数量', 'collected_data': ''}))
        assert result['ok'] is False
        assert result['error']['code'] in {'AUTH_REQUIRED', 'PERMISSION_DENIED'}
    finally:
        current_identity.reset(token)


def test_warehouse_stale_tool_call_cannot_open_purchase_confirmation():
    middleware = _remaining_order_preflight({'order_update': {'allowed_decisions': ['approve', 'reject']}})
    token = current_identity.set({'role': 'warehouse'})
    try:
        state = {'messages': [AIMessage(content='', tool_calls=[{'name': 'order_update', 'args': {}, 'id': 'old', 'type': 'tool_call'}])]}
        result = middleware.after_model(state, None)
        assert result['jump_to'] == 'model'
        assert result['messages'][0].status == 'error'
        assert json.loads(result['messages'][0].content)['error']['code'] == 'PERMISSION_DENIED'
    finally:
        current_identity.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize('item,response', [
    ({'interrupt_type': 'order_info_supplement'}, {'supplement': '采购20件'}),
    ({'interrupt_type': 'hitl_approval', 'action_requests': [{'name': 'purchase_request_create', 'args': {}}]}, {'decisions': [{'type': 'approve'}]}),
    ({'interrupt_type': 'hitl_approval', 'action_requests': [{'name': 'order_update', 'args': {}}]}, {'decisions': [{'type': 'approve'}]}),
    ({'interrupt_type': 'workflow_recovery', 'workflow_type': 'procurement'}, {'workflow_action': 'retry'}),
])
async def test_warehouse_cannot_resume_procurement_checkpoint(monkeypatch, item, response):
    async def pending(*_):
        return [dict(item, interrupt_id='blocked')]
    monkeypatch.setattr(chat, 'agent_loader', SimpleNamespace(get_pending_interrupts=pending))
    token = current_identity.set({'role': 'warehouse'})
    try:
        with pytest.raises(HTTPException) as exc:
            await chat._open_stream('denied-purchase', '3', 'storekeeper', resume={'interrupt_id': 'blocked', **response})
        assert exc.value.status_code == 403
        assert not chat._thread_locks.get('3:denied-purchase', SimpleNamespace(locked=lambda: False)).locked()
    finally:
        current_identity.reset(token)


@pytest.mark.asyncio
async def test_warehouse_can_still_confirm_inventory_work(monkeypatch):
    async def pending(*_):
        return [{'interrupt_id': 'stock', 'interrupt_type': 'hitl_approval', 'action_requests': [{'name': 'inventory_adjust', 'args': {}}]}]
    monkeypatch.setattr(chat, 'agent_loader', SimpleNamespace(get_pending_interrupts=pending))
    token = current_identity.set({'role': 'warehouse'})
    try:
        result = await chat._open_stream('stock-work', '3', 'storekeeper', resume={'interrupt_id': 'stock', 'decisions': [{'type': 'approve'}]})
        # Close the response without running synthetic business work.
        await result.body_iterator.aclose()
    finally:
        current_identity.reset(token)
