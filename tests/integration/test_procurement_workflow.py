"""Exercise real checkpointed procurement nodes without touching live business data."""
from datetime import date,timedelta
from decimal import Decimal

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from backend.auth import current_identity


class BusinessService:
    def __init__(self):
        self.calls=[];self.status=1;self.enabled=True;self.fail_submit=False
        self.verify_found=True;self.fail_query=False;self.saved=None

    async def __call__(self,name,args):
        self.calls.append((name,args.copy()))
        if self.fail_query and name=='part_query':
            return {'ok':False,'error':{'code':'ERP_UNAVAILABLE','retryable':True}}
        if name=='part_query':
            return {'ok':True,'data':{'records':[{'id':7,'part_code':'PC0007','name':'测试机油泵','supplier_id':2,'purchase_price':'0.10'}],'total':1,'current':1,'size':100}}
        if name=='supplier_query':
            return {'ok':True,'data':[{'id':2,'name':'真实供应商','supplier_code':'SUP00002','status':self.status}]}
        if name=='replenishment_suggestions':
            return {'ok':True,'data':{'records':[{'id':7,'enabled':self.enabled,'supplier_status':self.status,'current_quantity':100,'suggested':0}],'total':1}}
        if name=='purchase_request_create':
            self.saved=args.copy()
            if self.fail_submit:return {'ok':False,'error':{'code':'ERP_UNAVAILABLE','retryable':True}}
            return {'ok':True,'data':{'id':91,'status':'pending'}}
        if name=='order_create':
            self.saved=args.copy()
            if self.fail_submit:return {'ok':False,'error':{'code':'ERP_UNAVAILABLE','retryable':True}}
            return {'ok':True,'data':{'id':92,'orderNumber':args['order_number']}}
        if name=='procurement_verification':
            if not self.verify_found:return {'ok':True,'data':{'found':False}}
            s=self.saved
            if args['operation']=='purchase_request_create':
                row={**s,'id':91,'status':'pending','name':'测试机油泵','supplier_name':'真实供应商','supplier_id':2}
            else:
                row={'id':92,'order_number':s['order_number'],'status':1,'supplier_id':2,'expected_delivery_date':s['expected_delivery_date'],'total_amount':'0.30','lines':s['order_detail']}
            return {'ok':True,'data':{'found':True,'record':row}}
        raise AssertionError(name)


@pytest.fixture
def buyer():
    token=current_identity.set({'id':7,'username':'purchaser','display_name':'采购员','role':'purchaser','enabled':True})
    yield
    current_identity.reset(token)


def draft(**changes):
    return {'part_id':7,'quantity':3,'unit_price':'0.10','expected_date':(date.today()+timedelta(days=2)).isoformat(),'reason':'用户确认的额外需求','manual':True,**changes}


def graph(service):
    from agent.procurement_workflow import create_procurement_graph
    return create_procurement_graph(service,checkpointer=InMemorySaver())


async def start(service,action='purchase_request_create',data=None):
    g=graph(service);config={'configurable':{'thread_id':'isolated-procurement'}}
    result=await g.ainvoke({'action':action,'draft':data or draft()},config)
    return g,config,result


async def test_confirmed_manual_quantity_is_not_reduced_to_suggestion(buyer):
    service=BusinessService();g,c,r=await start(service)
    assert r['stage']=='awaiting_confirmation' and '__interrupt__' in r
    assert not any(n=='purchase_request_create' for n,_ in service.calls)
    result=await g.ainvoke(Command(resume={'decisions':[{'type':'approve'}]}),c)
    assert result['result']['ok'] is True
    assert result['result']['data']['quantity']==3
    assert Decimal(str(result['result']['data']['unit_price']))==Decimal('0.10')
    assert sum(n=='purchase_request_create' for n,_ in service.calls)==1
    assert service.calls[-1][0]=='procurement_verification'


async def test_supplier_terminated_does_not_ask_confirmation_or_write(buyer):
    service=BusinessService();service.status=3;g,c,r=await start(service)
    assert '__interrupt__' not in r
    assert r['result']['ok'] is False
    assert r['result']['error']['code']=='SUPPLIER_NOT_ACTIVE'
    assert not any(n=='purchase_request_create' for n,_ in service.calls)


async def test_disabled_material_cannot_be_submitted(buyer):
    service=BusinessService();service.enabled=False;_,_,r=await start(service)
    assert r['result']['error']['code']=='PART_DISABLED'
    assert not any(n=='purchase_request_create' for n,_ in service.calls)


async def test_missing_fields_resume_in_same_workflow_without_model_replanning(buyer):
    service=BusinessService();g,c,r=await start(service,data=draft(quantity=None,reason=None))
    assert r['stage']=='awaiting_information'
    fields=r['__interrupt__'][0].value['fields']
    assert any(f['name']=='reason' and f['required'] for f in fields)
    response={'values':{'0.orderDetail.0.quantity':8,'0.reason':'人工确认补货'}}
    r=await g.ainvoke(Command(resume=response),c)
    assert r['stage']=='awaiting_confirmation'
    r=await g.ainvoke(Command(resume={'decisions':[{'type':'approve'}]}),c)
    assert r['result']['ok'] and service.saved['quantity']==8
    assert service.saved['reason']=='人工确认补货'


async def test_user_reject_never_calls_business_write(buyer):
    service=BusinessService();g,c,_=await start(service)
    r=await g.ainvoke(Command(resume={'decisions':[{'type':'reject'}]}),c)
    assert r['result']['outcome']=='cancelled'
    assert not any(n=='purchase_request_create' for n,_ in service.calls)


async def test_supplier_is_rechecked_after_confirmation(buyer):
    service=BusinessService();g,c,_=await start(service)
    service.status=3
    r=await g.ainvoke(Command(resume={'decisions':[{'type':'approve'}]}),c)
    assert r['result']['error']['code']=='SUPPLIER_NOT_ACTIVE'
    assert not any(n=='purchase_request_create' for n,_ in service.calls)


async def test_write_timeout_verifies_landed_record_before_declaring_outcome(buyer):
    service=BusinessService();service.fail_submit=True;g,c,_=await start(service)
    r=await g.ainvoke(Command(resume={'decisions':[{'type':'approve'}]}),c)
    assert r['result']['ok'] and r['verified']
    assert sum(n=='purchase_request_create' for n,_ in service.calls)==1


async def test_unknown_write_pauses_and_retry_only_verifies_no_resubmit(buyer):
    service=BusinessService();service.fail_submit=True;service.verify_found=False
    g,c,_=await start(service)
    r=await g.ainvoke(Command(resume={'decisions':[{'type':'approve'}]}),c)
    assert r['stage']=='awaiting_recovery'
    assert r['__interrupt__'][0].value['write_attempted'] is True
    service.verify_found=True
    r=await g.ainvoke(Command(resume={'workflow_action':'retry'}),c)
    assert r['result']['ok'] and r['verified']
    assert sum(n=='purchase_request_create' for n,_ in service.calls)==1


async def test_read_fault_can_resume_without_submitting_business_write(buyer):
    service=BusinessService();service.fail_query=True;g,c,r=await start(service)
    assert r['stage']=='awaiting_recovery'
    assert r['__interrupt__'][0].value['write_attempted'] is False
    service.fail_query=False
    r=await g.ainvoke(Command(resume={'workflow_action':'retry'}),c)
    assert r['stage']=='awaiting_confirmation'
    assert not any(n=='purchase_request_create' for n,_ in service.calls)


async def test_role_revoked_during_pause_blocks_write(buyer):
    service=BusinessService();g,c,_=await start(service)
    t=current_identity.set({'id':7,'role':'warehouse','enabled':True})
    try:r=await g.ainvoke(Command(resume={'decisions':[{'type':'approve'}]}),c)
    finally:current_identity.reset(t)
    assert r['result']['error']['code']=='PERMISSION_DENIED'
    assert not any(n=='purchase_request_create' for n,_ in service.calls)


async def test_direct_order_has_fixed_unique_number_and_verified_actual_status(buyer):
    service=BusinessService()
    data={'supplier_id':2,'expected_delivery_date':draft()['expected_date'],'order_detail':[{'partId':7,'quantity':3,'unitPrice':'0.10'}]}
    g,c,r=await start(service,'order_create',data)
    r=await g.ainvoke(Command(resume={'decisions':[{'type':'approve'}]}),c)
    assert r['result']['ok'] and r['result']['data']['status']==1
    assert service.saved['order_number']
    assert r['result']['data']['total_amount']=='0.30'


async def test_ack_without_matching_record_never_becomes_success(buyer):
    service=BusinessService();service.verify_found=False;g,c,_=await start(service)
    r=await g.ainvoke(Command(resume={'decisions':[{'type':'approve'}]}),c)
    assert '__interrupt__' in r and not r.get('verified')
    assert not r.get('result',{}).get('ok')


async def test_duplicate_ack_is_verified_against_the_existing_real_order(buyer):
    service=BusinessService()
    async def duplicate_ack(name,args):
        value=await service(name,args)
        if name=='order_create':return {'ok':False,'error':{'code':'ERP_HTTP_ERROR','status':409,'retryable':False}}
        return value
    g=graph(duplicate_ack);c={'configurable':{'thread_id':'duplicate-safe'}}
    data={'supplier_id':2,'expected_delivery_date':draft()['expected_date'],'order_detail':[{'partId':7,'quantity':3,'unitPrice':'0.10'}]}
    await g.ainvoke({'action':'order_create','draft':data},c)
    r=await g.ainvoke(Command(resume={'decisions':[{'type':'approve'}]}),c)
    assert r['result']['ok'] and r['result']['verified']
    assert sum(n=='order_create' for n,_ in service.calls)==1


async def test_actual_request_supplier_must_match_confirmed_supplier(buyer):
    service=BusinessService()
    async def changed_supplier(name,args):
        result=await service(name,args)
        if name=='procurement_verification':result['data']['record']['supplier_id']=9
        return result
    g,c,_=await start(changed_supplier)
    r=await g.ainvoke(Command(resume={'decisions':[{'type':'approve'}]}),c)
    assert r['stage']=='awaiting_recovery' and not r['verified']
    assert sum(n=='purchase_request_create' for n,_ in service.calls)==1


@pytest.mark.parametrize('after_confirmation', [False, True])
async def test_existing_pending_request_stops_before_write_and_explains_next_step(buyer, after_confirmation):
    service=BusinessService()
    pending=not after_confirmation

    async def with_pending(name,args):
        result=await service(name,args)
        if name=='replenishment_suggestions':
            result['data']['records'][0]['pending_quantity']=20 if pending else 0
        return result

    g,c,result=await start(with_pending)
    if after_confirmation:
        assert '__interrupt__' in result
        pending=True
        result=await g.ainvoke(Command(resume={'decisions':[{'type':'approve'}]}),c)
    assert '__interrupt__' not in result
    assert result['result']['error']['code']=='PENDING_PURCHASE_REQUEST'
    assert '已有待审批申请' in result['result']['business']['execution']['message']
    assert not any(n=='purchase_request_create' for n,_ in service.calls)


@pytest.mark.parametrize('action', ['purchase_request_create','order_create'])
@pytest.mark.parametrize('decision', ['approve','reject'])
@pytest.mark.parametrize('delegated', [False, True])
async def test_creation_tool_ends_agent_after_one_confirmation_even_if_model_would_repeat(buyer, action, decision, delegated):
    """The real tool->nested graph->agent route must not call the model again."""
    import json
    from types import SimpleNamespace
    from langchain.agents import create_agent
    from langchain_core.language_models import BaseChatModel
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from agent.tools.procurement import create_procurement_tools

    service=BusinessService()
    raw_tools=[]
    for name in ('supplier_query','part_query','replenishment_suggestions','order_create',
                 'purchase_request_create','procurement_verification'):
        async def invoke(args, tool_name=name):return await service(tool_name,args)
        raw_tools.append(SimpleNamespace(name=name,ainvoke=invoke))
    tools=create_procurement_tools(raw_tools)
    args=draft() if action=='purchase_request_create' else {
        'supplier_id':2,'expected_delivery_date':draft()['expected_date'],
        'order_detail':[{'partId':7,'quantity':3,'unitPrice':'0.10'}]}

    class RepeatingModel(BaseChatModel):
        @property
        def _llm_type(self):return 'offline-repeated-purchase'
        def bind_tools(self, tools, **kwargs):return self
        def _generate(self,messages,stop=None,run_manager=None,**kwargs):
            calls=sum(isinstance(m,ToolMessage) for m in messages)
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content='',tool_calls=[{
                'id':f'purchase-{calls}','name':action,'args':args}]))])

    if delegated:
        from deepagents import create_deep_agent
        from agent.tools.business_answer import publish_business_answer
        from agent.subagents.loader import resolve_subagent_tools

        class DelegatingModel(RepeatingModel):
            def _generate(self,messages,stop=None,run_manager=None,**kwargs):
                returned=next((m for m in messages if isinstance(m,ToolMessage) and m.name=='task'),None)
                if returned is None:
                    call={'id':'delegate','name':'task','args':{'description':'发起采购','subagent_type':'procurement-test'}}
                else:
                    actual=json.loads(returned.content)
                    assert actual['outcome']==('success' if decision=='approve' else 'cancelled')
                    call={'id':'answer','name':'publish_business_answer','args':{'answer':'采购申请已提交，等待审批。' if decision=='approve' else '您已取消本次提交。'}}
                return ChatResult(generations=[ChatGeneration(message=AIMessage(content='',tool_calls=[call]))])

        specs=resolve_subagent_tools([dict(name='procurement-test',description='采购测试',system_prompt='采购',model=RepeatingModel(),tools=[t.name for t in tools])],tools)
        agent=create_deep_agent(model=DelegatingModel(),tools=[publish_business_answer],
            subagents=specs,
            checkpointer=InMemorySaver())
    else:
        agent=create_agent(RepeatingModel(),tools=tools,checkpointer=InMemorySaver())
    config={'configurable':{'thread_id':f'agent-{action}-{decision}'}}
    first=await agent.ainvoke({'messages':[HumanMessage(content='发起一笔采购')]},config)
    assert len(first['__interrupt__'])==1
    response={first['__interrupt__'][0].id:{'decisions':[{'type':decision}]}}
    result=await agent.ainvoke(Command(resume=response),config)
    assert not result.get('__interrupt__'), 'completed procurement must not open another confirmation'
    assert not (await agent.aget_state(config)).next
    assert sum(n==action for n,_ in service.calls)==int(decision=='approve')
    last=result['messages'][-1]
    assert isinstance(last,ToolMessage)
    if delegated:
        assert last.name=='publish_business_answer'
        assert ('等待审批' if decision=='approve' else '取消') in last.content
    else:
        actual=json.loads(last.content)
        assert actual['outcome']==('success' if decision=='approve' else 'cancelled')


def test_pending_request_has_specific_public_failure():
    from api_view.output_protocol import tool_failure
    result=tool_failure('purchase_request_create', {'ok':False,'error':{'code':'PENDING_PURCHASE_REQUEST'}})
    assert result['code']=='PENDING_PURCHASE_REQUEST'
    assert '已有待审批申请' in result['message']
    assert not result['retryable']
