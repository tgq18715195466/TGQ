import asyncio
from dataclasses import replace
import httpx
import pytest
from langchain_mcp_adapters.interceptors import MCPToolCallRequest
from backend.auth import current_identity, current_token, identity
from agent.tools.auth_bridge import authenticated_mcp_call
from mcp_server import http_base

@pytest.mark.asyncio
async def test_mcp_principals_are_request_local():
    async def invoke(uid, role, tool):
        user=identity(dict(id=uid,username=f'u{uid}',display_name='User',role=role))
        a=current_identity.set(user);b=current_token.set(f'token-{uid}')
        try:
            async def handler(request):
                await asyncio.sleep(0)
                return request.headers
            return await authenticated_mcp_call(MCPToolCallRequest(name=tool,args={},server_name='erp-api'),handler)
        finally:
            current_token.reset(b);current_identity.reset(a)
    buyer, warehouse, denied = await asyncio.gather(invoke(1,'purchaser','order_create'),invoke(2,'warehouse','inventory_adjust'),invoke(3,'warehouse','order_create'))
    assert buyer['Authorization']=='Bearer token-1'
    assert warehouse['Authorization']=='Bearer token-2'
    assert denied.isError and 'PERMISSION_DENIED' in denied.content[0].text
    assert current_identity.get() is None

@pytest.mark.asyncio
async def test_mcp_no_identity_and_external_token_isolation():
    async def handler(request):return request.headers
    request=MCPToolCallRequest(name='supplier_query',args={},server_name='erp-api')
    result=await authenticated_mcp_call(request,handler)
    assert result.isError and 'AUTH_REQUIRED' in result.content[0].text
    mark=current_token.set('secret')
    try:
        assert await authenticated_mcp_call(replace(request,server_name='analysis'),handler) is None
    finally:current_token.reset(mark)

@pytest.mark.asyncio
async def test_http_forwarding_discards_stale_and_forged_headers(monkeypatch):
    incoming={'authorization':'Bearer current'}
    monkeypatch.setattr(http_base,'get_http_headers',lambda **kwargs: incoming)
    request=httpx.Request('POST','http://localhost/api/orders/create',headers={'Authorization':'Bearer previous','Cookie':'erp_session=old','x-user-id':'admin','x-username':'spoof'})
    await http_base.forward_authenticated_user(request)
    assert request.headers['authorization']=='Bearer current'
    assert all(key not in request.headers for key in ['cookie','x-user-id','x-username'])
    incoming.clear()
    await http_base.forward_authenticated_user(request)
    assert 'authorization' not in request.headers
