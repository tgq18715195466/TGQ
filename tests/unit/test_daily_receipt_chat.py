import json
import pytest
from backend.auth import current_identity
from api_view.api import chat
from test_chat_business_results import Loader


@pytest.mark.parametrize('count', [0, 1])
async def test_today_receipts_only_queries_receipt_records(monkeypatch, count):
    loader=Loader()
    monkeypatch.setattr(chat,'agent_loader',loader)
    calls=[]
    async def query(name,args):
        calls.append((name,args))
        return {'ok':True,'data':{'date':'2026-10-08','records':[
            {'part_code':'P1','part_name':'活塞环组件','model':'标准型','supplier_name':'供方',
             'accepted':3,'unit':'套','received_at':'2026-10-08 08:00:00','order_number':'PO1'}
        ] if count else [],'total':count}}
    monkeypatch.setattr(chat,'invoke_readonly_tool',query)
    mark=current_identity.set({'id':3,'role':'warehouse','enabled':True})
    try:
        events=[json.loads(s[6:]) async for s in chat.stream_chat_response('今日物料汇总','daily')]
    finally:current_identity.reset(mark)
    assert len(calls)==1 and calls[0][0]=='daily_receipts'
    assert loader.stream_options is None, 'fixed daily summary must not launch broad model queries'
    final=events[-1]
    if count:
        assert final['business_results'][0]['records'][0]['part_name']=='活塞环组件'
        assert '入库' in final['content']
    else:
        assert final['content']=='今日没有物料入库。'
        assert final['business_results']==[]


async def test_daily_query_failure_never_means_no_receipts(monkeypatch):
    monkeypatch.setattr(chat,'agent_loader',Loader())
    async def query(*args):return {'ok':False,'error':{'code':'ERP_UNAVAILABLE','retryable':True}}
    monkeypatch.setattr(chat,'invoke_readonly_tool',query)
    events=[json.loads(s[6:]) async for s in chat.stream_chat_response('今日入库物料汇总','daily-failed')]
    assert '没有物料入库' not in events[-1]['content']
    assert any(e['type']=='tool_error' for e in events)


def test_intent_is_specific_and_does_not_swallow_other_requests():
    from api_view.daily_receipts import is_daily_receipt_request
    assert is_daily_receipt_request('请帮我查询今天入库的物料汇总', 'warehouse')
    assert is_daily_receipt_request('今日物料汇总','warehouse')
    assert not is_daily_receipt_request('今日物料汇总','purchaser')
    for text in ('今日库存预警','今日采购汇总','今日入库汇总并采购20个','昨天入库物料汇总'):
        assert not is_daily_receipt_request(text,'warehouse')


async def test_incomplete_receipt_response_never_claims_empty(monkeypatch):
    monkeypatch.setattr(chat,'agent_loader',Loader())
    async def query(*args):return {'ok':True,'data':{'records':[],'total':5}}
    monkeypatch.setattr(chat,'invoke_readonly_tool',query)
    events=[json.loads(s[6:]) async for s in chat.stream_chat_response('今日入库物料汇总','daily-invalid')]
    assert '没有物料入库' not in events[-1]['content']
    assert any(e['type']=='tool_error' for e in events)


async def test_daily_date_uses_beijing_day_at_utc_boundary(monkeypatch):
    from datetime import datetime, timezone
    from api_view import daily_receipts
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):return datetime(2026,10,7,16,30,tzinfo=timezone.utc).astimezone(tz)
    monkeypatch.setattr(daily_receipts,'datetime',Clock)
    calls=[]
    async def query(name,args):
        calls.append(args)
        return {'ok':True,'data':{'records':[],'total':0}}
    _=[event async for event in daily_receipts.receipt_summary_stream(query)]
    assert calls==[{'date':'2026-10-08'}]
