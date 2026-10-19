"""Checkpointed procurement steps. Models supply a draft, never execution decisions."""
from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Awaitable, TypedDict
from uuid import uuid4

from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt
from langgraph.config import get_stream_writer
from pydantic import ValidationError

from backend.auth import current_identity, can
from backend.app import OrderCreate
from backend.operations import PurchaseRequest
from agent.order_fields import build_order_form, _parse_value, order_validation_message


class ProcurementState(TypedDict, total=False):
    action: str
    draft: dict
    stage: str
    request_id: str
    facts: dict
    missing: list[str]
    prepared: dict
    result: dict
    fault: dict | None
    acknowledgement: dict
    confirmed: bool
    write_attempted: bool
    verified: bool
    recovery_target: str


STAGES = {
    'identifying':'正在整理您的采购需求。',
    'querying':'正在查询供应商和物料资料。',
    'validating':'正在核验合作状态、物料归属和采购字段。',
    'awaiting_information':'请补充采购所需信息。',
    'awaiting_confirmation':'请确认本次采购提交信息。',
    'submitting':'正在提交您已确认的采购需求。',
    'verifying':'正在读取业务单据，核验真实提交结果。',
    'awaiting_recovery':'请核验执行状态后继续处理。',
    'complete':'已整理采购处理结果。',
}


def stage(name):
    try:get_stream_writer()({'type':'procurement_progress','stage':name,'message':STAGES[name]})
    except RuntimeError:pass  # Unit helpers can run outside a graph.
    return {'stage':name}


def failure(code, message, *, outcome='failure'):
    return {'ok':False,'outcome':outcome,'error':{'code':code,'detail':message,'retryable':False}}


def decode(value):
    if hasattr(value,'content'):value=value.content
    if isinstance(value,str):
        try:return json.loads(value)
        except ValueError:return {'ok':False,'error':{'code':'INVALID_RESPONSE'}}
    if isinstance(value,list) and value and all(isinstance(x,dict) and x.get('type')=='text' for x in value):
        return decode(value[0].get('text','')) if len(value)==1 else {'ok':False,'error':{'code':'INVALID_RESPONSE'}}
    if isinstance(value,list):return {'ok':True,'data':value}
    return value if isinstance(value,dict) else {'ok':False,'error':{'code':'INVALID_RESPONSE'}}


def data_rows(value):
    payload=value.get('data',{})
    if isinstance(payload,list):return payload
    return payload.get('records',[]) if isinstance(payload,dict) else []


def pick(row,*names):
    return next((row[n] for n in names if n in row and row[n] is not None),None)


def resolve_material(catalog, pid, name=None, code=None, *, confirmed=False):
    """Only queried identities may become IDs; never strip a business code's prefix."""
    name = name.strip() if isinstance(name, str) else None
    code = code.strip() if isinstance(code, str) else None
    def id_key(value):
        if isinstance(value, bool):return None
        text = str(value).strip()
        return int(text) if text.isascii() and text.isdecimal() and int(text) > 0 else None
    key = id_key(pid)
    by_id = next((p for p in catalog if key is not None and id_key(pick(p, 'id', 'partId')) == key), None)
    def matches(part):
        return (not name or pick(part, 'name', 'part_name', 'partName') == name) and (
            not code or pick(part, 'part_code', 'partCode') == code)
    if by_id is not None:
        if matches(by_id):return by_id, None
        return None, failure('PART_IDENTITY_MISMATCH', '物料名称、编码与内部 ID 不一致，请核实后重新选择。')
    # Confirmation authorizes a particular record, never a replacement with the same name.
    if confirmed or not (name or code):
        return None, failure('PART_NOT_FOUND', '未找到指定物料，请核实完整物料编码。') if pid is not None else None
    candidates = [p for p in catalog if matches(p)]
    if len(candidates) == 1:return candidates[0], None
    if len(candidates) > 1:
        return None, failure('PART_AMBIGUOUS', '匹配到多个同名物料，请提供完整物料编码后重新发起采购。')
    if name and code and any(pick(p, 'name', 'part_name', 'partName') == name or pick(p, 'part_code', 'partCode') == code for p in catalog):
        return None, failure('PART_IDENTITY_MISMATCH', '物料名称与编码不一致，请核实后重新选择。')
    return None, failure('PART_NOT_FOUND', '未找到指定物料，请核实完整物料编码或准确名称。')


def order_draft(action,draft):
    """The form reuses the existing order field layout for both procurement entrances."""
    if action=='purchase_request_create':
        return {'supplierId':draft.get('supplier_id'),'expectedDeliveryDate':draft.get('expected_date'),
            'orderDetail':[{'partId':draft.get('part_id'),'quantity':draft.get('quantity'),'unitPrice':draft.get('unit_price')}],
            'reason':draft.get('reason')}
    aliases={'supplier_id':'supplierId','expected_delivery_date':'expectedDeliveryDate','order_detail':'orderDetail',
        'order_number':'orderNumber','remark':'remark','total_amount':'totalAmount','status':'status',
        'order_time':'orderTime','actual_delivery_date':'actualDeliveryDate'}
    return {aliases[k]:v for k,v in draft.items() if k in aliases}


def supplement_form(action,draft,missing):
    form=build_order_form(orders=[order_draft(action,draft)])
    if action=='purchase_request_create':
        form['fields'].append({'path':'0.reason','name':'reason','label':'补货原因','type':'text','required':True,
            'value':draft.get('reason'),'order_index':0,'detail_index':None})
    return {'type':'order_info_request','workflow_type':'procurement','workflow_action':action,
        'missing_fields':'\n'.join(missing),'collected_data':'请补充以下表单中缺少或不符合要求的字段。',**form}


def validate_workflow_supplement(item,response):
    """Use checkpoint-owned field definitions, never a client supplied schema."""
    values=response.get('values')
    if not isinstance(values,dict):raise ValueError('请通过采购信息表单补充字段；不确定时可先取消并重新描述需求。')
    allowed={f['path']:f for f in item.get('fields',[])}
    if not allowed or any(k not in allowed for k in values):raise ValueError('采购字段已失效或包含未知字段，请刷新后重试。')
    normalized={}
    for key,value in values.items():
        field=allowed[key]
        if value is None or isinstance(value,str) and not value.strip():
            if field.get('required'):raise ValueError(field['label']+'：必填')
            normalized[key]=None
        else:normalized[key]=_parse_value(field,value)
    note=response.get('supplement','')
    if not isinstance(note,str):raise ValueError('补充说明必须为文本。')
    return {'values':normalized,'supplement':note.strip()}


def merge_supplement(action,draft,response):
    if not isinstance(response,dict) or not isinstance(response.get('values'),dict):
        raise ValueError('请在信息补全表单中填写采购字段。')
    updated=deepcopy(draft)
    order=order_draft(action,updated)
    order.setdefault('orderDetail',[{}])
    if not order['orderDetail']:order['orderDetail']=[{}]
    for path,value in response['values'].items():
        pieces=path.split('.')
        if pieces[0]!='0':raise ValueError('此流程每次只处理一笔采购需求。')
        if len(pieces)==4 and pieces[1]=='orderDetail':
            index=int(pieces[2])
            if index>=len(order['orderDetail']):raise ValueError('订单明细已变化，请刷新表单。')
            order['orderDetail'][index][pieces[3]]=value
        elif len(pieces)==2:order[pieces[1]]=value
        else:raise ValueError('采购字段格式无效。')
    if action=='purchase_request_create':
        line=order['orderDetail'][0]
        updated.update(part_id=line.get('partId'),quantity=line.get('quantity'),unit_price=line.get('unitPrice'),
            supplier_id=order.get('supplierId'),expected_date=order.get('expectedDeliveryDate'),reason=order.get('reason'))
    else:
        reverse={'supplierId':'supplier_id','expectedDeliveryDate':'expected_delivery_date','orderDetail':'order_detail',
            'orderNumber':'order_number','totalAmount':'total_amount','orderTime':'order_time','actualDeliveryDate':'actual_delivery_date'}
        updated.update({reverse.get(k,k):v for k,v in order.items()})
    return updated


def snapshot_matches(action,prepared,record,expected_supplier_id=None):
    try:
        if action=='purchase_request_create':
            if expected_supplier_id is not None and int(record['supplier_id'])!=int(expected_supplier_id):return False
            return (int(record['part_id'])==int(prepared['part_id']) and int(record['quantity'])==int(prepared['quantity'])
                and Decimal(str(record['unit_price']))==Decimal(str(prepared['unit_price']))
                and str(record['expected_date'])[:10]==prepared['expected_date'])
        if int(pick(record,'supplier_id','supplierId'))!=int(prepared['supplier_id']):return False
        if pick(record,'order_number','orderNumber')!=prepared['order_number']:return False
        if str(pick(record,'expected_delivery_date','expectedDeliveryDate'))[:10]!=prepared['expected_delivery_date']:return False
        actual=record.get('lines',[]);wanted=prepared['order_detail']
        def line_key(line):return (int(pick(line,'part_id','partId')),int(line['quantity']),Decimal(str(pick(line,'unit_price','unitPrice'))))
        return len(actual)==len(wanted) and sorted(map(line_key,actual))==sorted(map(line_key,wanted))
    except (KeyError,ValueError,TypeError,InvalidOperation):return False


def create_procurement_graph(invoke:Callable[[str,dict],Awaitable[Any]], *, checkpointer=None):
    """Compile fixed nodes; the surrounding Agent supplies its persistent checkpointer."""
    async def call(name,args):
        try:return decode(await invoke(name,{k:v for k,v in args.items() if v is not None}))
        except (TimeoutError,ConnectionError):return {'ok':False,'error':{'code':'ERP_UNAVAILABLE','retryable':True}}

    def identify(state):
        draft=deepcopy(state['draft']);request_id=uuid4().hex
        draft.pop('request_id',None);draft.pop('actor',None);draft.pop('created_by',None)
        if state['action']=='order_create' and not draft.get('order_number'):
            today=datetime.now(timezone(timedelta(hours=8))).strftime('%Y%m%d')
            draft['order_number']='PO'+today+uuid4().hex[:12].upper()
        return {**stage('identifying'),'draft':draft,'request_id':request_id,'write_attempted':False,'verified':False,'confirmed':False,'fault':None}

    async def query(state):
        stage('querying')
        if not can(current_identity.get(),'procurement'):
            return {'result':failure('PERMISSION_DENIED','当前账号没有采购提交权限。'),'fault':None,'stage':'complete'}
        draft=deepcopy(state['draft']);action=state['action'];facts={'parts':{},'supplier':None,'suggestions':{}}
        details=order_draft(action,draft).get('orderDetail') or [{}]
        # Collect complete pages instead of assuming a first-page absence proves non-existence.
        catalog=[];page=1
        has_id=any(pick(d,'partId','part_id') is not None for d in details)
        if has_id or draft.get('part_name') or draft.get('part_code'):
            while True:
                # A name filter would hide an existing but conflicting ID.
                raw=await call('part_query',{'current':page,'size':100,
                    'name':None if has_id or draft.get('part_code') else draft.get('part_name')})
                if raw.get('ok') is not True:return {'fault':raw,'recovery_target':'query','stage':'querying'}
                rows=data_rows(raw);catalog.extend(rows)
                payload=raw.get('data',{});total=payload.get('total') if isinstance(payload,dict) else None
                if total is None or len(catalog)>=total:break
                if not rows or page>=100:return {'fault':failure('INVALID_RESPONSE','物料分页数据不完整，请检查业务服务。'),'recovery_target':'query'}
                page+=1
        for i,line in enumerate(details):
            pid=pick(line,'partId','part_id')
            found,error=resolve_material(catalog,pid,draft.get('part_name'),draft.get('part_code'),confirmed=state.get('confirmed',False))
            if error:return {'result':error,'fault':None}
            if not found:continue
            pid=pick(found,'id','partId');facts['parts'][str(pid)]=found
            if action=='purchase_request_create':draft['part_id']=pid
            else:
                line['partId']=pid
                draft.setdefault('order_detail',details)
            if not draft.get('supplier_id'):draft['supplier_id']=pick(found,'supplier_id','supplierId')
            eligible=await call('replenishment_suggestions',{'part_id':pid,'demand':draft.get('demand',0)})
            if eligible.get('ok') is not True:return {'fault':eligible,'recovery_target':'query'}
            erows=data_rows(eligible)
            item=next((r for r in erows if pick(r,'id','part_id')==pid),None)
            if not item:return {'fault':failure('INVALID_RESPONSE','业务服务未返回物料准入信息。'),'recovery_target':'query'}
            facts['parts'][str(pid)]['enabled']=item.get('enabled')
            facts['suggestions'][str(pid)]=item
        if draft.get('supplier_id') or draft.get('supplier_name'):
            raw=await call('supplier_query',{'supplier_id':draft.get('supplier_id'),'name':draft.get('supplier_name','')})
            if raw.get('ok') is not True:return {'fault':raw,'recovery_target':'query'}
            suppliers=data_rows(raw)
            supplier=next((r for r in suppliers if r.get('id')==draft.get('supplier_id')),None) if draft.get('supplier_id') else (suppliers[0] if len(suppliers)==1 else None)
            if supplier:facts['supplier']=supplier;draft['supplier_id']=supplier['id']
            elif draft.get('supplier_id'):return {'result':failure('SUPPLIER_NOT_FOUND','供应商不存在，请核实供应商编号。'),'fault':None}
        return {'draft':draft,'facts':facts,'fault':None,'stage':'validating'}

    def eligibility(state):
        stage('validating');facts=state.get('facts',{});supplier=facts.get('supplier')
        if supplier and supplier.get('status')!=1:return {'result':failure('SUPPLIER_NOT_ACTIVE','供应商不在合作中，无法提交采购。')}
        for part in facts.get('parts',{}).values():
            if part.get('enabled') not in (True,1):return {'result':failure('PART_DISABLED','物料已停用或准入状态无法核实，不能提交采购。')}
            if supplier and pick(part,'supplier_id','supplierId')!=supplier['id']:
                return {'result':failure('SUPPLIER_PART_MISMATCH','物料不属于所选供应商，请重新选择。')}
        if state['action']=='purchase_request_create':
            for suggestion in facts.get('suggestions',{}).values():
                if Decimal(str(suggestion.get('pending_quantity') or 0))>0:
                    return {'result':failure('PENDING_PURCHASE_REQUEST',
                        '该物料已有待审批申请，请到采购申请页面处理原申请，勿重复提交。')}
        missing=[];prepared={};draft=state['draft'];action=state['action']
        try:
            if action=='purchase_request_create':
                keys=('part_id','quantity','unit_price','expected_date','reason','demand','manual')
                raw={k:draft[k] for k in keys if draft.get(k) is not None}
                raw.setdefault('manual',True)
                validated=PurchaseRequest.model_validate({**raw,'actor':'authenticated-user','request_id':state['request_id']})
                prepared=validated.model_dump(mode='json',exclude={'actor'})
            else:
                validated=OrderCreate.model_validate(order_draft(action,draft))
                aliases={'supplierId':'supplier_id','expectedDeliveryDate':'expected_delivery_date','orderDetail':'order_detail','orderNumber':'order_number',
                    'totalAmount':'total_amount','orderTime':'order_time','actualDeliveryDate':'actual_delivery_date'}
                prepared={aliases.get(k,k):v for k,v in validated.model_dump(mode='json',by_alias=True,exclude_none=True).items() if k!='createdBy'}
        except ValidationError as exc:
            labels={'part_id':'物料','quantity':'采购数量','unit_price':'采购单价','expected_date':'预计交货日期','reason':'补货原因','demand':'额外需求量'}
            for error in exc.errors(include_input=False):
                key=str(error['loc'][-1]) if error['loc'] else ''
                missing.append(labels.get(key,'采购字段')+'：'+order_validation_message(error))
        if not supplier:missing.append('请选择或补充可核实的合作供应商。')
        if not facts.get('parts'):missing.append('请选择或补充可核实的物料。')
        return {'missing':missing,'prepared':prepared,'stage':'validating'}

    def prepare_information(state):return stage('awaiting_information')
    def collect(state):
        value=supplement_form(state['action'],state['draft'],state['missing'])
        response=interrupt(value)
        try:return {'draft':merge_supplement(state['action'],state['draft'],response),'missing':[],'prepared':{}}
        except ValueError as exc:return {'result':failure('MISSING_INFORMATION',str(exc),outcome='needs_information')}

    def prepare_confirmation(state):return stage('awaiting_confirmation')
    def confirm(state):
        response=interrupt({'action_requests':[{'name':state['action'],'args':state['prepared']}],
            'review_configs':[{'action_name':state['action'],'allowed_decisions':['approve','reject']}],
            'workflow_type':'procurement'})
        decisions=response.get('decisions',[]) if isinstance(response,dict) else []
        if len(decisions)!=1 or decisions[0].get('type')!='approve':
            return {'confirmed':False,'result':failure('CANCELLED','您已取消本次提交，未执行采购写入。',outcome='cancelled')}
        return {'confirmed':True}

    async def recheck(state):
        update=await query(state)
        if update.get('fault') or update.get('result'):return update
        checked={**state,**update};validation=eligibility(checked)
        if validation.get('missing'):
            return {'result':failure('ORDER_VALIDATION_FAILED','采购资料或日期已变化，请重新确认采购信息。')}
        return {**update,**validation}

    async def submit(state):
        stage('submitting')
        if not can(current_identity.get(),'procurement'):return {'result':failure('PERMISSION_DENIED','当前账号没有采购提交权限。')}
        ack=await call(state['action'],state['prepared'])
        return {'acknowledgement':ack,'write_attempted':True,'stage':'verifying'}

    async def verify(state):
        stage('verifying');prepared=state['prepared']
        raw=await call('procurement_verification',{'operation':state['action'],'request_id':state['request_id'],
            'order_number':prepared.get('order_number')})
        payload=raw.get('data')
        payload=payload if isinstance(payload,dict) else {}
        expected_supplier=state.get('facts',{}).get('supplier') or {}
        if raw.get('ok') is True and payload.get('found') and snapshot_matches(state['action'],prepared,payload.get('record',{}),expected_supplier.get('id')):
            record=payload['record']
            return {'result':{'ok':True,'data':record,'verified':True,'outcome':'success','verificationOutcome':'success'},'verified':True,'fault':None,'stage':'complete'}
        ack=state.get('acknowledgement',{})
        if raw.get('ok') is True and payload.get('found') is False and ack.get('ok') is False and ack.get('error',{}).get('retryable') is False:
            # A negative acknowledgement plus an authoritative absent record
            # confirms rejection. A conflict may instead refer to a landed
            # order after checkpoint replay, so verify before reporting failure.
            return {'result':ack,'verified':False,'fault':None,'stage':'complete'}
        return {'fault':failure('EXECUTION_UNKNOWN','尚无法确认实际单据与本次提交一致，请核验单据后继续。',outcome='unknown'),
            'recovery_target':'verify','verified':False,'stage':'verifying'}

    def prepare_recovery(state):return stage('awaiting_recovery')
    def recovery(state):
        wrote=state.get('write_attempted',False)
        response=interrupt({'type':'workflow_recovery','workflow_type':'procurement','write_attempted':wrote,
            'message':'提交结果暂未核实。重新核验只读取单据，不会再次提交。' if wrote else '读取业务资料失败，尚未提交采购。您可以重新查询或取消。'})
        if isinstance(response,dict) and response.get('workflow_action')=='retry':return {'fault':None}
        return {'result':failure('EXECUTION_UNKNOWN' if wrote else 'CANCELLED',
            '您已停止核验。采购执行结果仍未确认，请先检查业务单据，避免重复采购。' if wrote else '您已取消本次流程，未提交采购。',
            outcome='unknown' if wrote else 'cancelled')}

    def respond(state):
        result=state.get('result') or failure('EXECUTION_UNKNOWN','实际执行结果尚未确认。',outcome='unknown')
        # A typed result must come from actual API data, never the draft's computed status.
        from agent.business_results import normalize_business_result
        business=normalize_business_result(state['action'],result)
        if business is not None:result={**result,'business':business.model_dump(mode='json')}
        return {**stage('complete'),'result':result}

    def routed(state,next_node):
        return 'respond' if state.get('result') else ('prepare_recovery' if state.get('fault') else next_node)

    builder=StateGraph(ProcurementState)
    for name,node in [('identify',identify),('query_master',query),('validate_eligibility',eligibility),
        ('prepare_information',prepare_information),('collect_fields',collect),('prepare_confirmation',prepare_confirmation),
        ('confirm',confirm),('recheck_master',recheck),('submit',submit),('verify_result',verify),
        ('prepare_recovery',prepare_recovery),('recover',recovery),('respond',respond)]:builder.add_node(name,node)
    builder.add_edge(START,'identify');builder.add_edge('identify','query_master')
    builder.add_conditional_edges('query_master',lambda s:routed(s,'validate_eligibility'))
    builder.add_conditional_edges('validate_eligibility',lambda s:'respond' if s.get('result') else ('prepare_information' if s['missing'] else 'prepare_confirmation'))
    builder.add_edge('prepare_information','collect_fields')
    builder.add_conditional_edges('collect_fields',lambda s:'respond' if s.get('result') else 'query_master')
    builder.add_edge('prepare_confirmation','confirm');builder.add_conditional_edges('confirm',lambda s:'recheck_master' if s.get('confirmed') else 'respond')
    builder.add_conditional_edges('recheck_master',lambda s:routed(s,'submit'))
    builder.add_conditional_edges('submit',lambda s:'respond' if s.get('result') else 'verify_result')
    builder.add_conditional_edges('verify_result',lambda s:routed(s,'respond'))
    builder.add_edge('prepare_recovery','recover')
    builder.add_conditional_edges('recover',lambda s:'respond' if s.get('result') else ('verify_result' if s['recovery_target']=='verify' else 'query_master'))
    builder.add_edge('respond',END)
    return builder.compile(checkpointer=checkpointer)
