"""Deterministic daily inbound summary using real receipt evidence only."""
import asyncio
from datetime import datetime, timedelta, timezone
import json
import re
import uuid

from langchain_core.messages import AIMessageChunk, ToolMessage
from agent.business_results import normalize_business_result
from api_view.output_protocol import decode_result


def is_daily_receipt_request(message, role=None):
    text=re.sub(r'[\s，,。！？!?：:]+','',message or '')
    text=re.sub(r'^(?:请您?|帮我|给我|麻烦|查询|查看|统计|汇总|列出|显示)+','',text)
    if re.fullmatch(r'(?:今日|今天|当天)(?:的)?(?:入库|收货入库)(?:的)?(?:物料)?(?:情况|信息|汇总|明细|列表|记录|有哪些)?',text):
        return True
    return role=='warehouse' and bool(re.fullmatch(r'(?:今日|今天|当天)(?:的)?物料(?:情况|汇总|明细)',text))


async def receipt_summary_stream(invoke):
    call_id='daily-receipts-'+uuid.uuid4().hex
    args={'date':datetime.now(timezone(timedelta(hours=8))).date().isoformat()}
    def message(token):return {'type':'messages','ns':(),'data':(token,{})}
    yield message(AIMessageChunk(content='',tool_call_chunks=[{'name':'daily_receipts','id':call_id,'index':0,'args':json.dumps(args)}]))
    try:
        raw=decode_result(await asyncio.wait_for(invoke('daily_receipts',args),timeout=45))
    except Exception:
        raw={'ok':False,'error':{'code':'ERP_UNAVAILABLE','retryable':True}}
    result=normalize_business_result('daily_receipts',raw)
    if result is None or result.execution.outcome!='success' or not result.complete:
        text='今日入库记录查询未完成，请稍后重试。'
        if not isinstance(raw,dict) or raw.get('ok') is not False:
            raw={'ok':False,'error':{'code':'INVALID_RESPONSE','retryable':True}}
    elif not result.records:
        text='今日没有物料入库。'
    else:
        kinds=len({r.get('part_id') or r.get('part_code') or r.get('part_name') for r in result.records})
        text=f'今日有{kinds}种物料入库，共{len(result.records)}条验收入库记录，具体信息如下。'
    yield message(ToolMessage(name='daily_receipts',tool_call_id=call_id,content=json.dumps(raw,ensure_ascii=False,default=str)))
    answer_id='daily-answer-'+uuid.uuid4().hex
    yield message(AIMessageChunk(content='',tool_call_chunks=[{'name':'publish_business_answer','id':answer_id,'index':0,'args':json.dumps({'answer':text},ensure_ascii=False)}]))
    yield {'type':'values','ns':(),'data':{'messages':[ToolMessage(name='publish_business_answer',tool_call_id=answer_id,content=text)]}}
