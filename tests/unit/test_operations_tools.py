import asyncio
from types import SimpleNamespace
import httpx
from mcp_server.tools.operations_tools import forward,register_operations_tools

def test_request_tools_never_expose_approve_tool():
    names=[]
    class Registry:
        def tool(self,name):
            names.append(name)
            return lambda fn:fn
    register_operations_tools(Registry())
    assert set(names)=={'master_change_request','purchase_request_create','replenishment_suggestions','daily_receipts'}

def test_business_error_is_returned_and_request_id_preserved():
    seen=[]
    async def run():
        def handler(request):
            seen.append(request)
            return httpx.Response(409,json={'detail':'duplicate request'})
        async with httpx.AsyncClient(base_url='http://test/api',transport=httpx.MockTransport(handler)) as client:
            ctx=SimpleNamespace(request_context=SimpleNamespace(lifespan_context={'http_client':client}))
            result=await forward(ctx,'POST','requests',{'request_id':'stable'})
            assert not result['ok'] and not result['error']['retryable']
    asyncio.run(run())
    assert seen[0].url.path=='/api/operations/requests'
    assert b'stable' in seen[0].content
