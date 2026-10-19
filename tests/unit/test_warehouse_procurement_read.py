"""Warehouse reads procurement through the same policy as browser and Agent."""
from types import SimpleNamespace
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend import auth
from agent.tools.auth_bridge import authenticated_mcp_call,tool_allowed_for_identity
from agent.middlewares.context_injection import ContextInjectionMiddleware
from langchain_mcp_adapters.interceptors import MCPToolCallRequest

READS=['/api/dashboard/suppliers','/api/dashboard/orders','/api/dashboard/orders/1',
       '/api/suppliers/search','/api/orders/search-details','/api/operations/requests',
       '/api/operations/suggestions','/api/operations/receipts/daily']
WRITES=[('POST','/api/orders/create'),('PUT','/api/orders/update/1'),
        ('POST','/api/operations/requests'),('POST','/api/operations/requests/1/review'),
        ('POST','/api/operations/receiving/1/review'),('POST','/api/operations/receiving/1/start'),
        ('POST','/api/operations/master-changes'),('POST','/api/operations/master-changes/1/review')]

@pytest.mark.parametrize('method,path,status',[('GET',p,200) for p in READS]+[(m,p,403) for m,p in WRITES])
def test_warehouse_route_boundary(method,path,status):
 app=FastAPI();calls=[]
 @app.api_route('/{path:path}',methods=['GET','POST','PUT'])
 def endpoint():calls.append(True);return {'ok':True}
 app.add_middleware(auth.AuthMiddleware,authenticator=lambda token:auth.identity(dict(id=3,username='warehouse',display_name='Warehouse',role='warehouse')))
 result=TestClient(app).request(method,path,headers={'Authorization':'Bearer isolated'},json={})
 assert result.status_code==status
 assert bool(calls)==(status==200)

@pytest.mark.parametrize('name,allowed',[(n,True) for n in ['supplier_query','order_search_details','daily_receipts']]+[(n,False) for n in ['order_create','order_update','purchase_request_create','request_order_info','master_change_request','procurement_verification']])
async def test_agent_execution_uses_same_warehouse_permissions(name,allowed):
 user=auth.identity(dict(id=3,username='warehouse',display_name='Warehouse',role='warehouse'))
 assert tool_allowed_for_identity(name,user)==allowed
 marks=(auth.current_identity.set(user),auth.current_token.set('isolated'))
 calls=[]
 async def handler(request):calls.append(request);return 'read-only-result'
 try:
  result=await authenticated_mcp_call(MCPToolCallRequest(name=name,args={},server_name='erp-api'),handler)
  assert bool(calls)==allowed
  if allowed:assert result=='read-only-result'
  else:assert result.isError
 finally:
  auth.current_identity.reset(marks[0]);auth.current_token.reset(marks[1])

def test_context_explicitly_allows_procurement_queries_but_not_writes():
 mark=auth.current_identity.set(auth.identity(dict(id=3,username='warehouse',display_name='Warehouse',role='warehouse')))
 try:
  result=ContextInjectionMiddleware().before_agent({},SimpleNamespace(context=SimpleNamespace(user_id='3',username='warehouse')))
  text=result['messages'][0].content
  assert '可以只读查询采购订单、采购申请和供应商信息' in text
  assert '不得发起采购申请、创建或修改采购订单' in text
  assert '不得审批采购申请或订单' in text
 finally:auth.current_identity.reset(mark)

@pytest.mark.parametrize('role',['warehouse','purchaser','approver','admin'])
def test_daily_receipts_follows_inventory_read_permission(role):
 assert tool_allowed_for_identity('daily_receipts',{'role':role})
 assert not tool_allowed_for_identity('daily_receipts',{'role':role,'enabled':False})
