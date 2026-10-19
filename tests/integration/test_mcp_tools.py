from types import SimpleNamespace

import pytest

from agent.business_results import BusinessResult

from mcp_server.tools.inventory_tools import register_inventory_tools
from mcp_server.tools.order_tools import register_order_tools
from mcp_server.tools.parts_tools import register_parts_tools
from mcp_server.tools.suppliers_tools import register_supplier_tools


def assert_envelope(result, expected):
    """Retain old payload/error assertions while validating the new sidecar."""
    payload = {key: value for key, value in result.items() if key != 'business'}
    expected = {**expected}
    expected.setdefault('data', None)
    assert payload == expected
    business = BusinessResult.model_validate(result['business'])
    if not expected['ok']:
        assert business.execution.outcome in ('failure', 'unknown')
        assert business.records == []
    return business


class Registry:
    def __init__(self):
        self.tools = {}

    def tool(self, name):
        def decorate(function):
            self.tools[name] = function
            return function
        return decorate


class Response:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        return None

    def json(self):
        return {"code": 200, "data": self._data}


class Client:
    def __init__(self):
        self.calls = []

    async def get(self, path, **kwargs):
        self.calls.append(("GET", path, kwargs))
        return Response([])

    async def post(self, path, **kwargs):
        self.calls.append(("POST", path, kwargs))
        return Response({"id": 1})

    async def put(self, path, **kwargs):
        self.calls.append(("PUT", path, kwargs))
        return Response({"id": 1})


@pytest.fixture
def registry():
    value = Registry()
    register_supplier_tools(value)
    register_parts_tools(value)
    register_order_tools(value)
    register_inventory_tools(value)
    return value


def _ctx(client):
    return SimpleNamespace(request_context=SimpleNamespace(lifespan_context={"http_client": client}))


def test_all_nine_mcp_tools_are_registered(registry):
    assert set(registry.tools) == {
        "supplier_query", "part_query", "part_search", "part_by_supplier",
        "order_create", "order_update", "order_search_details", "inventory_warning", "inventory_adjust",
    }


@pytest.mark.asyncio
async def test_supplier_query_uses_name_parameter(registry):
    client = Client()
    result = await registry.tools["supplier_query"]("博世", _ctx(client))
    assert client.calls == [("GET", "/suppliers/search", {"params": {"name": "博世", "paged": True, "current": 1, "size": 100}})]
    assert_envelope(result, {"ok": True, "data": []})


@pytest.mark.asyncio
async def test_status_only_update_sends_no_generated_fields(registry):
    client = Client()
    result = await registry.tools["order_update"](order_id=7, status=4, ctx=_ctx(client))
    assert client.calls == [("PUT", "/orders/update/7", {"json": {"status": 4}})]
    assert_envelope(result, {"ok": True, "data": {"id": 1}})

@pytest.mark.asyncio
async def test_inventory_adjust_forwards_stable_request_id(registry):
    client = Client()
    result = await registry.tools['inventory_adjust'](part_id=1, delta=-2, reason='synthetic', request_id='test-key', ctx=_ctx(client))
    assert client.calls == [('POST', '/inventory/adjust', {'json': {'partId': 1, 'delta': -2, 'reason': 'synthetic', 'requestId': 'test-key'}})]
    assert_envelope(result, {'ok': True, 'data': {'id': 1}})


@pytest.mark.asyncio
async def test_inventory_adjust_fastmcp_schema():
    from fastmcp import FastMCP
    mcp = FastMCP('inventory-test')
    register_inventory_tools(mcp)
    tools = await mcp.list_tools()
    tool = next(t for t in tools if t.name == 'inventory_adjust')
    assert set(tool.parameters['required']) == {'part_id', 'delta', 'reason', 'request_id'}
    assert tool.parameters['properties']['delta']['type'] == 'integer'

@pytest.mark.asyncio
@pytest.mark.parametrize('status,code,retryable', [(404, 'INVENTORY_NOT_FOUND', False), (409, 'IDEMPOTENCY_CONFLICT', False), (422, 'VALIDATION', False), (500, 'INVENTORY_ADJUST_FAILED', True)])
async def test_inventory_adjust_preserves_http_errors(registry, status, code, retryable):
    import httpx
    class ErrorClient:
        async def post(self, path, **kwargs):
            return httpx.Response(status, json={'detail': {'code': code}}, request=httpx.Request('POST', 'http://test/inventory/adjust'))
    result = await registry.tools['inventory_adjust'](part_id=1, delta=1, reason='test', request_id='key', ctx=_ctx(ErrorClient()))
    assert_envelope(result, {'ok': False, 'error': {'code': code, 'status': status, 'retryable': retryable}})


@pytest.mark.asyncio
async def test_inventory_adjust_network_retry_keeps_key(registry):
    import httpx
    class Unavailable:
        async def post(self, path, **kwargs):
            assert kwargs['json']['requestId'] == 'stable-key'
            raise httpx.ReadTimeout('synthetic timeout')
    result = await registry.tools['inventory_adjust'](part_id=1, delta=1, reason='test', request_id='stable-key', ctx=_ctx(Unavailable()))
    assert_envelope(result, {'ok': False, 'error': {'code': 'ERP_UNAVAILABLE', 'retryable': True}})


@pytest.mark.asyncio
async def test_supplier_exact_code_forwarded(registry):
    client=Client()
    await registry.tools['supplier_query'](supplier_code='SUP00015',ctx=_ctx(client))
    assert client.calls==[('GET','/suppliers/search',{'params':{'name':'','paged':True,'current':1,'size':100,'supplierCode':'SUP00015'}})]

@pytest.mark.asyncio
async def test_create_required_fields_forwarded(registry):
    client=Client()
    await registry.tools['order_create'](supplier_id=15,expected_delivery_date='2099-01-01',order_detail=[{'partId':1,'quantity':2,'unitPrice':10}],ctx=_ctx(client))
    body=client.calls[0][2]['json']
    assert body['supplierId']==15 and body['expectedDeliveryDate']=='2099-01-01'

@pytest.mark.asyncio
@pytest.mark.parametrize('name,args',[('part_query',{}),('part_search',{'name':'合成'}),('part_by_supplier',{'supplier_id':1}),('inventory_warning',{}),('order_search_details',{})])
async def test_read_tools_return_structured_failure_not_fake_data(registry,name,args):
    import httpx
    class Unavailable:
        async def get(self,*a,**kw): raise httpx.ConnectError('offline')
    result=await registry.tools[name](**args,ctx=_ctx(Unavailable()))
    assert_envelope(result, {'ok':False,'error':{'code':'ERP_UNAVAILABLE','retryable':True}})

@pytest.mark.asyncio
@pytest.mark.parametrize('status,retryable',[(422,False),(503,True)])
async def test_query_http_failure_is_not_mislabeled_network_error(registry,status,retryable):
    import httpx
    class Rejected:
        async def get(self,path,**kwargs):
            return httpx.Response(status,json={'detail':'invalid query'},request=httpx.Request('GET','http://test/parts/page'))
    result=await registry.tools['part_query'](ctx=_ctx(Rejected()))
    assert result['error']['code']=='ERP_API_ERROR'
    assert result['error']['retryable']==retryable
