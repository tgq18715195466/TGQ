"""Resolve business codes and names without guessing database IDs or changing confirmed parts."""
from copy import deepcopy

import pytest
from langgraph.types import Command
from backend.auth import current_identity
from test_procurement_workflow import BusinessService, buyer, draft, start


class MaterialService(BusinessService):
    def __init__(self):
        super().__init__()
        self.parts = [dict(id=13, part_code='FC3001', name='前减震器', supplier_id=2)]

    async def __call__(self, name, args):
        if name == 'part_query':
            self.calls.append((name, args.copy()))
            rows = [p for p in self.parts if args.get('name', '') in p['name']]
            offset = (args['current'] - 1) * args['size']
            return {'ok': True, 'data': {'records': deepcopy(rows[offset:offset + args['size']]), 'total': len(rows)}}
        if name == 'replenishment_suggestions':
            self.calls.append((name, args.copy()))
            return {'ok': True, 'data': {'records': [{'id': args['part_id'], 'enabled': True}]}}
        return await super().__call__(name, args)


@pytest.mark.parametrize('role', ['purchaser', 'approver'])
@pytest.mark.parametrize('action', ['purchase_request_create', 'order_create'])
@pytest.mark.parametrize('identity', [
    {'part_id': 3001, 'part_name': '前减震器'},
    {'part_id': None, 'part_name': '前减震器'},
    {'part_id': None, 'part_code': 'FC3001'},
    {'part_id': '13', 'part_name': '前减震器'},
])
async def test_material_resolves_to_real_id_before_confirmation_and_write(role, action, identity):
    token = current_identity.set({'id': 7, 'role': role, 'enabled': True})
    try:
        service = MaterialService()
        data = draft(**identity)
        if action == 'order_create':
            data = {k: v for k, v in identity.items() if k != 'part_id'} | {
                'expected_delivery_date': draft()['expected_date'],
                'order_detail': [{'partId': identity['part_id'], 'quantity': 3, 'unitPrice': '0.10'}]}
        graph, config, state = await start(service, action, data)
        assert state['stage'] == 'awaiting_confirmation'
        prepared = state['__interrupt__'][0].value['action_requests'][0]['args']
        assert (prepared['part_id'] if action == 'purchase_request_create' else prepared['order_detail'][0]['partId']) == 13
        assert service.saved is None
        result = await graph.ainvoke(Command(resume={'decisions': [{'type': 'approve'}]}), config)
        assert result['result']['ok'] and result['verified']
        assert all(args['part_id'] == 13 for name, args in service.calls if name == 'replenishment_suggestions')
    finally:
        current_identity.reset(token)


@pytest.mark.parametrize('case,code', [
    ('conflicting_id', 'PART_IDENTITY_MISMATCH'),
    ('duplicate_name', 'PART_AMBIGUOUS'),
    ('conflicting_code', 'PART_IDENTITY_MISMATCH'),
    ('missing', 'PART_NOT_FOUND'),
])
async def test_uncertain_identity_never_writes_or_reports_generic_unknown(buyer, case, code):
    service = MaterialService()
    data = draft(part_id=3001, part_name='前减震器')
    if case == 'conflicting_id':
        service.parts.append(dict(id=3001, part_code='PC0001', name='其他物料', supplier_id=2))
    elif case == 'duplicate_name':
        service.parts.append(dict(id=14, part_code='FC3002', name='前减震器', supplier_id=2))
    elif case == 'conflicting_code':
        data.update(part_code='FC3002')
    else:
        service.parts = []
    _, _, state = await start(service, data=data)
    assert state['result']['error']['code'] == code
    expected = {'PART_IDENTITY_MISMATCH': '不一致', 'PART_AMBIGUOUS': '同名', 'PART_NOT_FOUND': '不存在'}[code]
    assert expected in state['result']['business']['execution']['message']
    from api_view.output_protocol import tool_failure
    assert expected in tool_failure('purchase_request_create', state['result'])['message']
    assert service.saved is None


async def test_confirmed_material_cannot_be_replaced_by_same_name(buyer):
    service = MaterialService()
    graph, config, state = await start(service, data=draft(part_id=3001, part_name='前减震器'))
    assert state['prepared']['part_id'] == 13
    service.parts[0]['id'] = 14
    state = await graph.ainvoke(Command(resume={'decisions': [{'type': 'approve'}]}), config)
    assert not state['result']['ok']
    assert service.saved is None


async def test_exact_name_wins_over_partial_name_and_pagination_is_complete(buyer):
    service = MaterialService()
    service.parts = [dict(id=100+i, part_code=f'OTHER{i}', name='前减震器支架', supplier_id=2) for i in range(100)] + service.parts
    _, _, state = await start(service, data=draft(part_id=3001, part_name='前减震器'))
    assert state['prepared']['part_id'] == 13
    assert len([1 for name, _ in service.calls if name == 'part_query']) == 2


async def test_full_code_disambiguates_same_name(buyer):
    service = MaterialService()
    service.parts.append(dict(id=14, part_code='FC3002', name='前减震器', supplier_id=2))
    _, _, state = await start(service, data=draft(part_id=3001, part_name='前减震器', part_code='FC3001'))
    assert state['prepared']['part_id'] == 13


def test_tool_schema_separates_code_from_internal_id():
    from agent.tools.procurement import RequestDraft, OrderDraft
    for schema in (RequestDraft, OrderDraft):
        assert schema.model_validate({'part_code': 'FC3001'}).part_code == 'FC3001'
    assert '数据库' in RequestDraft.model_fields['part_id'].description
