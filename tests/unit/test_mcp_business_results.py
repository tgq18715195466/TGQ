"""Offline tests for MCP evidence envelopes and unchanged HTTP forwarding."""
from copy import deepcopy
import json
from types import SimpleNamespace

import httpx
import pytest

from agent.business_results import BusinessResult, expose_business_result
from mcp_server.tools.inventory_tools import register_inventory_tools
from mcp_server.tools.operations_tools import forward, register_operations_tools
from mcp_server.tools.order_tools import register_order_tools
from mcp_server.tools.parts_tools import register_parts_tools
from mcp_server.tools.suppliers_tools import register_supplier_tools


class Registry:
    def __init__(self):
        self.tools = {}

    def tool(self, name):
        def decorate(function):
            self.tools[name] = function
            return function
        return decorate


def ctx(client):
    return SimpleNamespace(request_context=SimpleNamespace(lifespan_context={'http_client': client}))


def registry():
    value = Registry()
    for register in (register_supplier_tools, register_parts_tools, register_order_tools, register_inventory_tools, register_operations_tools):
        register(value)
    return value


CASES = [
    ('daily_receipts', {'date':'2026-10-08'}, 'GET', '/api/operations/receipts/daily', {'date':'2026-10-08','records':[{'part_code':'P1','part_name':'活塞环组件','accepted':3,'received_at':'2026-10-08 08:00:00'}],'total':1}),
    ('supplier_query', {}, 'GET', '/api/suppliers/search', [{'id': 1, 'name': '供方', 'status': 2}]),
    ('part_query', {}, 'GET', '/api/parts/page', {'records': [{'id': 3, 'name': '物料'}], 'total': 81, 'current': 2, 'size': 10}),
    ('part_search', {'name': '物料'}, 'GET', '/api/parts/search', [{'id': 3, 'name': '物料'}]),
    ('part_by_supplier', {'supplier_id': 1}, 'GET', '/api/parts/supplier/1', [{'id': 3, 'name': '物料'}]),
    ('order_search_details', {}, 'GET', '/api/orders/search-details', [{'id': 4, 'quantity': 3, 'unit_price': 0.1, 'status': 5}]),
    ('inventory_warning', {}, 'GET', '/api/inventory/warning', [{'id': 5, 'current_quantity': 1}]),
    ('order_create', {'supplier_id': 1, 'expected_delivery_date': '2099-01-01', 'order_detail': [{'partId': 3, 'quantity': 1, 'unitPrice': 1}]}, 'POST', '/api/orders/create', {'id': 6, 'status': 1}),
    ('order_update', {'order_id': 6, 'status': 4}, 'PUT', '/api/orders/update/6', {'id': 6, 'status': 3}),
    ('inventory_adjust', {'part_id': 3, 'delta': 1, 'reason': '测试', 'request_id': 'stable'}, 'POST', '/api/inventory/adjust', {'partId': 3, 'delta': 1, 'beforeQuantity': 1, 'afterQuantity': 2, 'requestId': 'stable'}),
    ('replenishment_suggestions', {'part_id': 3}, 'GET', '/api/operations/suggestions', {'records': [{'id': 3, 'name': '物料', 'suggested': 4}], 'total': 1}),
    ('master_change_request', {'entity': 'supplier', 'values': {'name': '模型输入'}, 'reason': '测试', 'request_id': 'stable'}, 'POST', '/api/operations/master-changes', {'id': 7, 'status': 'pending'}),
    ('purchase_request_create', {'part_id': 3, 'quantity': 3, 'unit_price': 0.1, 'expected_date': '2099-01-01', 'reason': '测试', 'request_id': 'stable'}, 'POST', '/api/operations/requests', {'id': 8, 'status': 'pending'}),
]


@pytest.mark.asyncio
@pytest.mark.parametrize('name,args,method,path,data', CASES)
async def test_every_tool_envelopes_original_data_and_preserves_http(name, args, method, path, data):
    seen = []
    before = deepcopy(data)
    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=data if '/operations/' in path else {'code': 200, 'data': data})
    async with httpx.AsyncClient(base_url='http://offline/api/', transport=httpx.MockTransport(handler), headers={'Authorization': 'Bearer current'}) as client:
        output = await registry().tools[name](**args, ctx=ctx(client))
    assert output['ok'] is True and output['data'] == before
    assert data == before
    assert seen[0].method == method and seen[0].url.path == path
    assert seen[0].headers['Authorization'] == 'Bearer current'
    business = BusinessResult.model_validate(output['business'])
    ui = expose_business_result(business)
    if method != 'GET':
        assert business.verificationOutcome == 'unknown' and ui['execution']['outcome'] == 'unknown'
    else:
        assert ui['execution']['outcome'] == 'success'
    if name == 'part_query':
        assert ui['total'] == 81 and ui['page'] == {'current': 2, 'size': 10} and not ui['complete']
    assert 'stable' not in json.dumps(ui, ensure_ascii=False)


@pytest.mark.asyncio
@pytest.mark.parametrize('name,args,method,path,data', CASES)
async def test_all_tool_failures_carry_sanitized_business(name, args, method, path, data):
    def handler(request):
        raise httpx.ReadTimeout('http://secret/token?credential=private', request=request)
    async with httpx.AsyncClient(base_url='http://offline/api/', transport=httpx.MockTransport(handler)) as client:
        output = await registry().tools[name](**args, ctx=ctx(client))
    assert output['ok'] is False and output['data'] is None
    assert output['business']['execution']['outcome'] == 'unknown'
    assert 'secret' not in str(output) and 'credential' not in str(output)


@pytest.mark.asyncio
@pytest.mark.parametrize('status,outcome', [(409, 'failure'), (422, 'failure'), (503, 'unknown')])
async def test_operation_http_failure_distinguishes_rejection_from_uncertainty(status, outcome):
    def handler(request):
        return httpx.Response(status, json={'detail': {'code': 'VALIDATION', 'input': 'private', 'message': '<script>bad</script>'}})
    async with httpx.AsyncClient(base_url='http://offline/api/', transport=httpx.MockTransport(handler)) as client:
        output = await forward(ctx(client), 'POST', 'requests', {'request_id': 'stable'})
    assert output['business']['execution']['outcome'] == outcome
    assert output['data'] is None
    assert 'private' not in str(output) and '<script>' not in str(output)


@pytest.mark.asyncio
async def test_inventory_local_validation_known_failure_without_http():
    class NoHTTP:
        async def post(self, *args, **kwargs):
            pytest.fail('invalid input must not be forwarded')
    output = await registry().tools['inventory_adjust'](part_id=1, delta=0, reason='测试', request_id='stable', ctx=ctx(NoHTTP()))
    assert output['ok'] is False
    assert output['business']['execution']['outcome'] == 'failure'


@pytest.mark.asyncio
@pytest.mark.parametrize('body', [{'code': 200}, {'data': []}, {'code': 503, 'message': 'internal secret'}])
async def test_malformed_or_uncertain_api_result_not_fake_empty_success(body):
    def handler(request):
        return httpx.Response(200, json=body)
    async with httpx.AsyncClient(base_url='http://offline/api/', transport=httpx.MockTransport(handler)) as client:
        output = await registry().tools['part_search'](name='物料', ctx=ctx(client))
    assert output['business']['execution']['outcome'] == 'unknown'
    assert output['business']['records'] == []
    assert 'secret' not in str(output)


@pytest.mark.asyncio
async def test_api_validated_business_rejection_is_failure():
    def handler(request):
        return httpx.Response(200, json={'code': 422, 'message': 'private validation body'})
    async with httpx.AsyncClient(base_url='http://offline/api/', transport=httpx.MockTransport(handler)) as client:
        output = await registry().tools['order_update'](order_id=1, status=4, ctx=ctx(client))
    assert output['ok'] is False
    assert output['business']['execution']['outcome'] == 'failure'
    assert 'private' not in str(output)


@pytest.mark.asyncio
@pytest.mark.parametrize('detail,code,expected', [
    ('该物料已有待审批申请，请先处理原申请','PENDING_PURCHASE_REQUEST','已有待审批申请'),
    ('供应商不在合作中，不能申请采购','SUPPLIER_NOT_ACTIVE','供应商不在合作中'),
    ('库存或预警值未知，请先维护库存和主数据','INVENTORY_DATA_INCOMPLETE','库存或预警值'),
    ('申请数量超过当前补货建议，请刷新或核实额外需求','QUANTITY_EXCEEDS_SUGGESTION','申请数量超过'),
])
async def test_known_procurement_rejections_keep_specific_safe_reason(detail,code,expected):
    def handler(request):return httpx.Response(409,json={'detail':detail})
    async with httpx.AsyncClient(base_url='http://offline/api/',transport=httpx.MockTransport(handler)) as client:
        output=await forward(ctx(client),'POST','requests',{'request_id':'stable'})
    assert output['error']['code']==code
    assert output['business']['execution']['outcome']=='failure'
    assert expected in output['business']['execution']['message']


@pytest.mark.asyncio
@pytest.mark.parametrize('name,args,path', [
    ('purchase_request_create', {'part_id': 1, 'quantity': 1, 'unit_price': 1, 'expected_date': '2099-01-01', 'reason': '测试', 'request_id': 'stable'}, '/operations/requests'),
    ('order_create', {'supplier_id': 1, 'expected_delivery_date': '2099-01-01', 'order_detail': []}, '/orders/create'),
    ('inventory_adjust', {'part_id': 1, 'delta': 1, 'reason': '测试', 'request_id': 'stable'}, '/inventory/adjust'),
])
async def test_known_http_rejection_survives_nonobject_error_body(name, args, path):
    def handler(request):
        assert request.url.path.endswith(path)
        return httpx.Response(409, json=['invalid body'])
    async with httpx.AsyncClient(base_url='http://offline/api/', transport=httpx.MockTransport(handler)) as client:
        output = await registry().tools[name](**args, ctx=ctx(client))
    assert output['ok'] is False
    assert output['business']['execution']['outcome'] == 'failure'


@pytest.mark.asyncio
@pytest.mark.parametrize('total', [0, 100, 101, 200, 205])
async def test_supplier_paged_reads_all_rows_without_truncation(total):
    rows = [{'id': i + 1, 'name': f'供方{i}', 'status': 1} for i in range(total)]
    seen = []
    def handler(request):
        seen.append(request)
        assert request.url.params['paged'] == 'true'
        assert request.url.params['size'] == '100'
        assert request.url.params['name'] == '供方'
        assert request.url.params['supplierCode'] == 'SUP_TEST'
        page = int(request.url.params['current'])
        assert len(seen) <= 4
        return httpx.Response(200, json={'code': 200, 'data': {'records': rows[(page - 1) * 100:page * 100], 'total': total, 'current': page, 'size': 100}})
    async with httpx.AsyncClient(base_url='http://offline/api/', transport=httpx.MockTransport(handler), headers={'Authorization': 'Bearer current'}) as client:
        output = await registry().tools['supplier_query'](name='供方', supplier_code='SUP_TEST', ctx=ctx(client))
    assert output['ok'] is True and output['data'] == {'records': rows, 'total': total}
    assert len(seen) == max(1, (total + 99) // 100)
    assert all(request.headers['Authorization'] == 'Bearer current' for request in seen)
    ui = expose_business_result(output['business'])
    assert ui['total'] == ui['returned_count'] == len(ui['records']) == total
    assert ui['complete'] is True


@pytest.mark.asyncio
@pytest.mark.parametrize('problem', ['empty', 'total_changed', 'duplicate', 'excess', 'wrong_page', 'legacy_midstream'])
async def test_supplier_inconsistent_page_is_recoverable_unknown(problem):
    first = [{'id': i + 1, 'name': '供方'} for i in range(100)]
    seen = []
    def handler(request):
        seen.append(request)
        assert len(seen) <= 3
        if len(seen) == 1:
            data = {'records': first, 'total': 101, 'current': 1, 'size': 100}
        else:
            data = {'records': [{'id': 101, 'name': '供方'}], 'total': 101, 'current': 2, 'size': 100}
            if problem == 'empty': data['records'] = []
            if problem == 'total_changed': data['total'] = 102
            if problem == 'duplicate': data['records'] = [first[0]]
            if problem == 'excess': data['records'].append({'id': 102, 'name': '供方'})
            if problem == 'wrong_page': data['current'] = 1
            if problem == 'legacy_midstream': data = [{'id': 101, 'name': '供方'}]
        return httpx.Response(200, json={'code': 200, 'data': data})
    async with httpx.AsyncClient(base_url='http://offline/api/', transport=httpx.MockTransport(handler)) as client:
        output = await registry().tools['supplier_query'](ctx=ctx(client))
    assert output['ok'] is False and output['error']['retryable'] is True
    assert output['business']['execution']['outcome'] == 'unknown'
    assert output['business']['complete'] is False
    assert len(seen) == 2


@pytest.mark.asyncio
async def test_supplier_second_page_transport_failure_preserves_unknown():
    seen = []
    def handler(request):
        seen.append(request)
        if len(seen) == 1:
            return httpx.Response(200, json={'code': 200, 'data': {'records': [{'id': i} for i in range(100)], 'total': 101, 'current': 1, 'size': 100}})
        raise httpx.ReadTimeout('secret token', request=request)
    async with httpx.AsyncClient(base_url='http://offline/api/', transport=httpx.MockTransport(handler)) as client:
        output = await registry().tools['supplier_query'](ctx=ctx(client))
    assert output['ok'] is False and output['error']['retryable'] is True
    assert output['business']['execution']['outcome'] == 'unknown'
    assert 'secret' not in str(output) and len(seen) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('malformed', ['json', 'envelope'])
async def test_supplier_malformed_page_is_recoverable_unknown(malformed):
    def handler(request):
        if malformed == 'json':
            return httpx.Response(200, content=b'not JSON')
        return httpx.Response(200, json={'data': {'records': [], 'total': 0}})
    async with httpx.AsyncClient(base_url='http://offline/api/', transport=httpx.MockTransport(handler)) as client:
        output = await registry().tools['supplier_query'](ctx=ctx(client))
    assert output['ok'] is False and output['error']['retryable'] is True
    assert output['business']['execution']['outcome'] == 'unknown'
