"""Transactional ERP operations. All identifiers in SQL are internal allowlists."""
import hashlib
import re
from backend.auth import current_identity
import json
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal
from uuid import uuid4

import pymysql
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator, ValidationError
from backend.dashboard import read

router = APIRouter(prefix='/api/operations', tags=['采购库存作业'])

BEIJING = timezone(timedelta(hours=8), 'Asia/Shanghai')


def receipt_read(sql, params=(), one=False):
    """A failed read is unavailable evidence, never an empty receipt list."""
    try:
        return read(sql, params, one)
    except pymysql.Error as exc:
        raise HTTPException(503, '收货数据库暂不可用，无法确认入库记录，请稍后重试') from exc


def receipt_storage_timezone():
    # Receipt DATETIME values are written by CURRENT_TIMESTAMP in the database
    # session's wall clock: native deployment is +08 and Docker is UTC.
    # Convert bounds and public timestamps; never rewrite stored receipt data.
    clock = receipt_read('SELECT TIMESTAMPDIFF(SECOND, UTC_TIMESTAMP(), NOW()) AS utc_offset', one=True)
    if not clock or clock.get('utc_offset') not in (0, 8 * 3600):
        raise HTTPException(503, '收货时间时区配置不一致，暂不能确认北京时间的入库记录')
    return timezone(timedelta(seconds=int(clock['utc_offset'])))


def receipt_time(value, storage_timezone=BEIJING):
    if value is None:
        return None
    timestamp = datetime.fromisoformat(str(value))
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=storage_timezone)
    return timestamp.astimezone(BEIJING).strftime('%Y-%m-%d %H:%M:%S')


@router.get('/receipts/daily')
def daily_receipts(date: str | None = Query(None, pattern=r'^\d{4}-\d{2}-\d{2}$')):
    """All actual accepted receipt lines in a half-open Beijing calendar day.

    No legacy completion flags, order dates or ordered quantities supply facts.
    Rows remain separate by batch and material; total describes the full list.
    """
    try:
        day = datetime.strptime(date, '%Y-%m-%d').date() if date else datetime.now(BEIJING).date()
        start = datetime.combine(day, datetime.min.time(), BEIJING)
        end = start + timedelta(days=1)
    except (ValueError, OverflowError) as exc:
        raise HTTPException(422, '日期必须为有效的 YYYY-MM-DD，且能计算次日边界') from exc
    storage_timezone = receipt_storage_timezone()
    records = receipt_read('''SELECT l.id AS receipt_line_id,g.id AS receipt_id,l.detail_id,l.part_id,
        p.part_code,p.name AS part_name,p.model,p.specification,p.category,p.unit,p.supplier_id,
        s.name AS supplier_name,s.supplier_code,l.accepted,g.created_at AS received_at,
        g.order_id,o.order_number,g.delivery_number
        FROM goods_receipt g JOIN goods_receipt_line l ON l.receipt_id=g.id
        LEFT JOIN part p ON p.id=l.part_id LEFT JOIN supplier s ON s.id=p.supplier_id
        LEFT JOIN purchase_order o ON o.id=g.order_id
        WHERE l.accepted>0 AND g.created_at>=%s AND g.created_at<%s
        ORDER BY g.created_at,g.id,l.id''', (start.astimezone(storage_timezone).strftime('%Y-%m-%d %H:%M:%S'), end.astimezone(storage_timezone).strftime('%Y-%m-%d %H:%M:%S')))
    by_unit = {}
    for row in records:
        row['received_at'] = receipt_time(row['received_at'], storage_timezone)
        unit = row.get('unit')
        by_unit[unit] = by_unit.get(unit, 0) + row['accepted']
    totals = {
        'accepted_quantity': sum(row['accepted'] for row in records),
        'receipt_count': len({row['receipt_id'] for row in records}),
        'order_count': len({row['order_id'] for row in records}),
        'part_count': len({row['part_id'] for row in records}),
        'by_unit': [{'unit': unit, 'accepted_quantity': quantity} for unit, quantity in by_unit.items()],
    }
    return {'date': day.isoformat(), 'timezone': 'Asia/Shanghai', 'start_at': start.isoformat(),
            'end_at': end.isoformat(), 'records': records, 'total': len(records), 'totals': totals}

def fail(message, status=409):
    raise HTTPException(status, message)

def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)

def decode(value):
    return json.loads(value) if isinstance(value, str) else value

@contextmanager
def transaction():
    from backend.app import _connect
    conn = _connect()
    try:
        conn.begin()
        with conn.cursor() as cur:
            yield cur
        conn.commit()
    except HTTPException:
        conn.rollback()
        raise
    except pymysql.IntegrityError as exc:
        conn.rollback()
        raise HTTPException(409, '编号已存在或数据冲突，请刷新后重试') from exc
    except pymysql.OperationalError as exc:
        conn.rollback()
        raise HTTPException(409, '数据正在被其他操作修改，请保留请求重试') from exc
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def once(cur, payload, kind):
    fingerprint = hashlib.sha256((kind+encode(payload.model_dump(mode='json'))).encode()).hexdigest()
    cur.execute('INSERT INTO erp_operation(request_id,payload_hash) VALUES(%s,%s) ON DUPLICATE KEY UPDATE request_id=VALUES(request_id)', (payload.request_id,fingerprint))
    cur.execute('SELECT payload_hash,result FROM erp_operation WHERE request_id=%s FOR UPDATE',(payload.request_id,))
    row=cur.fetchone()
    if row['payload_hash']!=fingerprint:fail('请求编号已用于其他内容，请勿重复使用')
    return decode(row['result']) if row['result'] is not None else None

def finish(cur, key, result):
    cur.execute('UPDATE erp_operation SET result=%s WHERE request_id=%s',(encode(result),key))
    return result

class Input(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)

class Operation(Input):
    request_id:str=Field(min_length=1,max_length=128)
    actor:str=Field(min_length=1,max_length=100)
    reason:str=Field(min_length=1,max_length=1000)

class ReceiptLine(Input):
    detail_id:int=Field(gt=0,strict=True)
    accepted:int=Field(ge=0,le=2147483647,strict=True)
    rejected:int=Field(ge=0,le=2147483647,strict=True)
    @model_validator(mode='after')
    def nonempty(self):
        if not self.accepted+self.rejected:raise ValueError('收货行的验收或拒收数量至少一项大于0')
        return self

class Receipt(Operation):
    delivery_number:str=Field(min_length=1,max_length=128)
    order_id:int=Field(gt=0,strict=True)
    lines:list[ReceiptLine]=Field(min_length=1,max_length=100)
    @model_validator(mode='after')
    def unique(self):
        if len({line.detail_id for line in self.lines})!=len(self.lines):raise ValueError('订单行不能重复')
        return self

class Stock(Operation):
    part_id:int=Field(gt=0,strict=True)
    kind:Literal['issue','return','count']
    quantity:int=Field(ge=0,le=2147483647,strict=True)
    expected_quantity:int|None=Field(None,ge=0,le=2147483647,strict=True)
    document:str=Field(min_length=1,max_length=128)
    recipient_name:str|None=Field(None,min_length=1,max_length=100)
    @model_validator(mode='after')
    def rules(self):
        if self.kind=='issue' and not self.recipient_name:raise ValueError('领料出库必须填写领料人姓名')
        if self.kind=='count' and self.expected_quantity is None:raise ValueError('盘点必须提供账面库存')
        if self.kind!='count' and self.quantity==0:raise ValueError('出入库数量必须大于0')
        return self

class Review(Input):
    actor:str=Field(min_length=1,max_length=100)
    decision:Literal['approve','reject']
    reason:str=Field(min_length=1,max_length=1000)

def lock_part(cur, part_id, active=True):
    cur.execute('SELECT p.*,COALESCE(pc.enabled,1) AS enabled FROM part p LEFT JOIN part_control pc ON pc.part_id=p.id WHERE p.id=%s AND p.deleted=0 FOR UPDATE',(part_id,))
    part=cur.fetchone()
    if not part:fail('物料不存在',404)
    if active and not part['enabled']:fail('物料已停用')
    return part

def stock_change(cur,part_id,delta,kind,reason,actor,document,expected=None,allow_create=False):
    if allow_create:
        cur.execute('INSERT IGNORE INTO inventory(part_id,current_quantity) VALUES(%s,0)',(part_id,))
    cur.execute('SELECT id,current_quantity FROM inventory WHERE part_id=%s AND deleted=0 FOR UPDATE',(part_id,))
    row=cur.fetchone()
    if not row:fail('物料未建库存记录，请先通过盘点建账')
    before=row['current_quantity']
    if expected is not None and before!=expected:fail('账面库存已变化，请刷新后重新盘点')
    after=before+delta
    if not 0<=after<=2147483647:fail('库存不足或数量超过上限')
    cur.execute('UPDATE inventory SET current_quantity=%s WHERE id=%s',(after,row['id']))
    cur.execute('INSERT INTO stock_movement(part_id,delta,before_quantity,after_quantity,kind,reason,actor,document) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',(part_id,delta,before,after,kind,reason,actor,document))
    return {'before_quantity':before,'after_quantity':after,'movement_id':cur.lastrowid}

def receipt_lines(cur,order_id,complete=False):
    cur.execute('SELECT d.id,d.part_id,d.quantity,d.unit_price,p.name,p.part_code,p.model,s.name AS supplier_name,s.supplier_code,COALESCE(r.accepted,0) AS accepted,COALESCE(r.rejected,0) AS rejected FROM order_detail d LEFT JOIN part p ON p.id=d.part_id LEFT JOIN supplier s ON s.id=p.supplier_id LEFT JOIN (SELECT detail_id,SUM(accepted) accepted,SUM(rejected) rejected FROM goods_receipt_line GROUP BY detail_id) r ON r.detail_id=d.id WHERE d.order_id=%s AND d.deleted=0 ORDER BY d.id',(order_id,))
    rows=cur.fetchall()
    for row in rows:
        row['remaining']=0 if complete else max(0,row['quantity']-row['accepted'])
    return rows

def order_version(order,lines):
    snapshot={k:order.get(k) for k in ['id','status','total_amount','expected_delivery_date','order_number','remark']}
    snapshot['lines']=[{k:r.get(k) for k in ['id','part_id','quantity','unit_price']} for r in lines]
    return hashlib.sha256(encode(snapshot).encode()).hexdigest()

@router.get('/receiving/{order_id}')
def receiving_detail(order_id:int):
    with transaction() as cur:
        cur.execute('SELECT * FROM purchase_order WHERE id=%s AND deleted=0',(order_id,))
        order=cur.fetchone()
        if not order:fail('订单不存在',404)
        lines=receipt_lines(cur,order_id,order['status']==4)
        order['review_version']=order_version(order,lines)
        order['lines']=lines
        order['receipt_state']='complete' if order['status']==4 else ('partial' if any(x['accepted'] for x in lines) else 'ordered')
        cur.execute('SELECT r.id,r.delivery_number,r.actor,r.reason,r.created_at,l.detail_id,l.accepted,l.rejected FROM goods_receipt r JOIN goods_receipt_line l ON l.receipt_id=r.id WHERE r.order_id=%s ORDER BY r.id DESC',(order_id,))
        order['receipts']=cur.fetchall()
        cur.execute('SELECT * FROM purchase_order_review WHERE order_id=%s',(order_id,))
        order['review']=cur.fetchone()
        return order

@router.post('/receipts')
def receive(payload:Receipt):
    with transaction() as cur:
        replay=once(cur,payload,'receipt')
        if replay is not None:return replay
        cur.execute('SELECT status FROM purchase_order WHERE id=%s AND deleted=0 FOR UPDATE',(payload.order_id,))
        order=cur.fetchone()
        if not order or order['status'] not in (2,3):fail('仅已审核或采购中的订单可收货')
        cur.execute('SELECT order_id FROM purchase_order_inventory_receipt WHERE order_id=%s',(payload.order_id,))
        if cur.fetchone():fail('该订单已有历史入库记录，不可再次入库')
        lines={x['id']:x for x in receipt_lines(cur,payload.order_id)}
        for line in payload.lines:
            item=lines.get(line.detail_id)
            if not item:fail('收货明细不属于该订单')
            if line.accepted+line.rejected>item['remaining']:fail('本批验收与拒收合计不能超过剩余数量')
        cur.execute('INSERT INTO goods_receipt(order_id,delivery_number,actor,reason) VALUES(%s,%s,%s,%s)',(payload.order_id,payload.delivery_number,payload.actor,payload.reason))
        rid=cur.lastrowid
        for line in sorted(payload.lines,key=lambda x:lines[x.detail_id]['part_id']):
            item=lines[line.detail_id]
            lock_part(cur,item['part_id'],active=False)
            cur.execute('INSERT INTO goods_receipt_line(receipt_id,detail_id,part_id,accepted,rejected) VALUES(%s,%s,%s,%s,%s)',(rid,line.detail_id,item['part_id'],line.accepted,line.rejected))
            if line.accepted:
                stock_change(cur,item['part_id'],line.accepted,'receipt',payload.reason,payload.actor,f'RECEIPT-{rid}/PO-{payload.order_id}',allow_create=True)
            item['accepted']+=line.accepted
        complete=bool(lines) and all(x['accepted']>=x['quantity'] for x in lines.values())
        cur.execute('UPDATE purchase_order SET status=%s,actual_delivery_date=%s WHERE id=%s',(4 if complete else 3,date.today() if complete else None,payload.order_id))
        if complete:cur.execute('INSERT INTO purchase_order_inventory_receipt(order_id) VALUES(%s)',(payload.order_id,))
        return finish(cur,payload.request_id,{'id':rid,'receipt_state':'complete' if complete else ('partial' if any(x['accepted'] for x in lines.values()) else 'ordered')})

@router.post('/stock')
def stock(payload:Stock):
    with transaction() as cur:
        replay=once(cur,payload,'stock')
        if replay is not None:return replay
        lock_part(cur,payload.part_id,active=payload.kind=='issue')
        delta=payload.quantity-payload.expected_quantity if payload.kind=='count' else payload.quantity*(-1 if payload.kind=='issue' else 1)
        result=stock_change(cur,payload.part_id,delta,payload.kind,payload.reason,payload.actor,payload.document,expected=payload.expected_quantity if payload.kind=='count' else None,allow_create=payload.kind=='count')
        if payload.kind=='issue':
            cur.execute('INSERT INTO stock_issue_recipient(movement_id,recipient_name) VALUES(%s,%s)',(result['movement_id'],payload.recipient_name))
        return finish(cur,payload.request_id,result)

@router.get('/movements')
def movements(part_id:int|None=Query(None,gt=0),current:int=Query(1,ge=1),size:int=Query(20,ge=1,le=100)):
    where='WHERE m.part_id=%s' if part_id else '';params=[part_id] if part_id else []
    total=read(f'SELECT COUNT(*) n FROM stock_movement m {where}',params,True)['n']
    rows=read(f'SELECT m.*,p.name,p.part_code,r.recipient_name FROM stock_movement m LEFT JOIN part p ON p.id=m.part_id LEFT JOIN stock_issue_recipient r ON r.movement_id=m.id {where} ORDER BY m.id DESC LIMIT %s OFFSET %s',params+[size,(current-1)*size])
    return {'records':rows,'total':total}

class SupplierValues(Input):
    supplier_code:str=Field(min_length=1,max_length=50)
    name:str=Field(min_length=1,max_length=100)
    status:int=Field(1,ge=1,le=3,strict=True)
    contact_person:str|None=Field(None,max_length=50)
    phone:str|None=Field(None,max_length=20)
    email:str|None=Field(None,max_length=100)
    address:str|None=Field(None,max_length=200)
    credit_rating:Literal['A','B','C','D']='B'

class PartValues(Input):
    part_code:str=Field(min_length=1,max_length=50)
    name:str=Field(min_length=1,max_length=100)
    supplier_id:int=Field(gt=0,strict=True)
    purchase_price:Decimal=Field(ge=0,max_digits=10,decimal_places=2)
    stock_warning_value:int=Field(ge=0,le=2147483647,strict=True)
    enabled:bool=True
    model:str|None=Field(None,max_length=100)
    unit:str=Field('个',min_length=1,max_length=20)
    category:str|None=Field(None,max_length=50)
    specification:str|None=Field(None,max_length=200)

class MasterChange(Operation):
    entity:Literal['supplier','part']
    entity_id:int|None=Field(None,gt=0,strict=True)
    values:dict
    @model_validator(mode='after')
    def fields(self):
        model=SupplierValues if self.entity=='supplier' else PartValues
        value=model.model_validate(self.values)
        self.values=value.model_dump(mode='json',exclude_unset=self.entity_id is not None)
        return self

def master_snapshot(cur,entity,entity_id):
    fields=list((SupplierValues if entity=='supplier' else PartValues).model_fields)
    selected=','.join('COALESCE(pc.enabled,1) AS enabled' if f=='enabled' else f't.{f}' for f in fields)
    join='LEFT JOIN part_control pc ON pc.part_id=t.id' if entity=='part' else ''
    cur.execute(f'SELECT t.id,{selected} FROM {entity} t {join} WHERE t.id=%s AND t.deleted=0 FOR UPDATE',(entity_id,))
    result=cur.fetchone()
    if not result:fail('主数据不存在',404)
    return result

@router.get('/master/{entity}')
def masters(entity:Literal['supplier','part'],q:str=Query('',max_length=100),current:int=Query(1,ge=1),size:int=Query(20,ge=1,le=100)):
    code='supplier_code' if entity=='supplier' else 'part_code'
    where=f't.deleted=0 AND (t.name LIKE %s OR t.{code} LIKE %s)';params=[f'%{q}%']*2
    join='LEFT JOIN part_control pc ON pc.part_id=t.id' if entity=='part' else ''
    selected='t.*'+(',COALESCE(pc.enabled,1) AS enabled' if entity=='part' else '')
    sort_direction = 'ASC' if entity == 'supplier' else 'DESC'
    return {'records':read(f'SELECT {selected} FROM {entity} t {join} WHERE {where} ORDER BY t.id {sort_direction} LIMIT %s OFFSET %s',params+[size,(current-1)*size]),'total':read(f'SELECT COUNT(*) n FROM {entity} t WHERE {where}',params,True)['n']}

def apply_master(cur,entity,entity_id,proposed,item_id):
    fields=dict(proposed)
    enabled=fields.pop('enabled',None)
    if entity=='part':
        cur.execute('SELECT id FROM supplier WHERE id=%s AND deleted=0 FOR UPDATE',(fields.get('supplier_id') or master_snapshot(cur,'part',entity_id)['supplier_id'],))
        if not cur.fetchone():fail('关联供应商不存在')
    if entity_id:
        cur.execute(f"UPDATE {entity} SET {','.join(f'{k}=%s' for k in fields)} WHERE id=%s",list(fields.values())+[entity_id])
    else:
        cur.execute(f"INSERT INTO {entity} ({','.join(fields)}) VALUES ({','.join(['%s']*len(fields))})",list(fields.values()))
        entity_id=cur.lastrowid
        cur.execute('UPDATE master_change SET entity_id=%s WHERE id=%s',(entity_id,item_id))
    if entity=='part' and enabled is not None:
        cur.execute('INSERT INTO part_control(part_id,enabled) VALUES(%s,%s) ON DUPLICATE KEY UPDATE enabled=VALUES(enabled)',(entity_id,enabled))
    return entity_id

@router.post('/master-changes')
def propose_master(payload:MasterChange):
    with transaction() as cur:
        replay=once(cur,payload,'master')
        if replay is not None:return replay
        previous=master_snapshot(cur,payload.entity,payload.entity_id) if payload.entity_id else None
        cur.execute('INSERT INTO master_change(entity,entity_id,proposed,previous,actor,reason) VALUES(%s,%s,%s,%s,%s,%s)',(payload.entity,payload.entity_id,encode(payload.values),encode(previous) if previous else None,payload.actor,payload.reason))
        change_id=cur.lastrowid
        user=current_identity.get() or {}
        direct=bool(payload.entity_id and user.get('role') in {'approver','admin'})
        if direct:
            entity_id=apply_master(cur,payload.entity,payload.entity_id,payload.values,change_id)
            cur.execute("UPDATE master_change SET status='applied',reviewed_at=NOW() WHERE id=%s",(change_id,))
            return finish(cur,payload.request_id,{'id':change_id,'status':'applied','entity_id':entity_id})
        return finish(cur,payload.request_id,{'id':change_id,'status':'pending'})

@router.get('/master-changes')
def master_changes(current:int=Query(1,ge=1),size:int=Query(20,ge=1,le=100)):
    result=read('SELECT * FROM master_change ORDER BY id DESC LIMIT %s OFFSET %s',(size,(current-1)*size))
    for r in result:
        r['proposed']=decode(r['proposed']);r['previous']=decode(r['previous'])
    return {'records':result,'total':read('SELECT COUNT(*) n FROM master_change',one=True)['n']}

def review_record(cur,table,item_id,payload):
    cur.execute(f'SELECT * FROM {table} WHERE id=%s FOR UPDATE',(item_id,))
    row=cur.fetchone()
    if not row:fail('申请不存在',404)
    expected='approved' if payload.decision=='approve' else 'rejected'
    if row['status']!='pending':
        if (row['status'],row['reviewer'],row['review_reason'])!=(expected,payload.actor,payload.reason):fail('申请已处理，不可重复变更审批结果')
        return row,True
    if row['actor']==payload.actor:fail('提交人与审批人不能相同')
    return row,False

def mark_review(cur,table,item_id,payload):
    cur.execute(f'UPDATE {table} SET status=%s,reviewer=%s,review_reason=%s,reviewed_at=NOW() WHERE id=%s',('approved' if payload.decision=='approve' else 'rejected',payload.actor,payload.reason,item_id))

@router.post('/master-changes/{item_id}/review')
def review_master(item_id:int,payload:Review):
    with transaction() as cur:
        row,replay=review_record(cur,'master_change',item_id,payload)
        if replay:return {'id':item_id,'status':row['status'],'entity_id':row['entity_id']}
        entity=row['entity'];entity_id=row['entity_id']
        if payload.decision=='approve':
            if entity_id:
                current=master_snapshot(cur,entity,entity_id)
                if encode(current)!=encode(decode(row['previous'])):fail('主数据已变化，请驳回旧申请并重新提交')
            entity_id=apply_master(cur,entity,entity_id,decode(row['proposed']),item_id)
        mark_review(cur,'master_change',item_id,payload)
        return {'id':item_id,'status':'approved' if payload.decision=='approve' else 'rejected','entity_id':entity_id}

SUGGESTION_SQL = '''SELECT p.id,p.part_code,p.name,p.supplier_id,p.purchase_price,p.stock_warning_value,
 s.name supplier_name,s.status supplier_status,COALESCE(pc.enabled,1) enabled,i.current_quantity,
 COALESCE(o.on_order,0) on_order,COALESCE(a.pending_quantity,0) pending_quantity
 FROM part p LEFT JOIN supplier s ON s.id=p.supplier_id AND s.deleted=0
 LEFT JOIN part_control pc ON pc.part_id=p.id LEFT JOIN inventory i ON i.part_id=p.id AND i.deleted=0
 LEFT JOIN (SELECT d.part_id,SUM(GREATEST(d.quantity-COALESCE(r.received,0),0)) on_order FROM order_detail d
 JOIN purchase_order po ON po.id=d.order_id AND po.deleted=0 AND po.status IN (1,2,3)
 LEFT JOIN (SELECT detail_id,SUM(accepted) received FROM goods_receipt_line GROUP BY detail_id) r ON r.detail_id=d.id
 WHERE d.deleted=0 GROUP BY d.part_id) o ON o.part_id=p.id
 LEFT JOIN (SELECT part_id,SUM(quantity) pending_quantity FROM purchase_request WHERE status='pending' GROUP BY part_id) a ON a.part_id=p.id
 WHERE p.deleted=0'''

def calculate(row,demand,exclude_pending=0):
    row['demand']=demand
    known=row['current_quantity'] is not None and row['stock_warning_value'] is not None
    row['suggested']=max(0,row['stock_warning_value']+demand-row['current_quantity']-int(row['on_order'])-int(row['pending_quantity'])+exclude_pending) if known else None
    from backend.procurement_eligibility import classify_purchase_readiness
    readiness=classify_purchase_readiness({**row,'safety_stock': row['stock_warning_value']+demand if row['stock_warning_value'] is not None else None})
    row['purchase_status_name']=readiness['purchase_status_name']
    row['suggested_request_quantity']=row['suggested'] if readiness['purchase_status_name']=='可采购' and row['suggested'] is not None else 0
    return row

@router.get('/suggestions')
def suggestions(part_id:int|None=Query(None,gt=0),demand:int=Query(0,ge=0,le=2147483647),q:str=Query('',max_length=100),current:int=Query(1,ge=1),size:int=Query(20,ge=1,le=100)):
    where=' AND p.id=%s' if part_id else ' AND (p.name LIKE %s OR p.part_code LIKE %s)'
    params=[part_id] if part_id else [f'%{q}%']*2
    rows=read(SUGGESTION_SQL+where+' ORDER BY p.id LIMIT %s OFFSET %s',params+[size,(current-1)*size])
    return {'records':[calculate(r,demand) for r in rows],'total':read('SELECT COUNT(*) n FROM part p WHERE p.deleted=0'+where,params,True)['n']}

class PurchaseRequest(Operation):
    manual:bool=Field(False,strict=True)
    part_id:int=Field(gt=0,strict=True)
    quantity:int=Field(gt=0,le=2147483647,strict=True)
    unit_price:Decimal=Field(ge=0,max_digits=10,decimal_places=2)
    expected_date:date
    demand:int=Field(0,ge=0,le=2147483647,strict=True)
    @model_validator(mode='after')
    def date_valid(self):
        if self.expected_date<date.today():raise ValueError('预计交货日期不能早于今天')
        if self.quantity*self.unit_price>Decimal('9999999999.99'):raise ValueError('订单金额超过上限')
        return self

def eligible_suggestion(cur,part_id,demand,exclude_pending=0):
    part=lock_part(cur,part_id)
    cur.execute('SELECT status FROM supplier WHERE id=%s AND deleted=0 FOR UPDATE',(part['supplier_id'],))
    supplier=cur.fetchone()
    if not supplier or supplier['status']!=1:fail('供应商不在合作中，不能申请采购')
    cur.execute('SELECT id FROM inventory WHERE part_id=%s AND deleted=0 FOR UPDATE',(part_id,))
    cur.fetchone()
    cur.execute(SUGGESTION_SQL+' AND p.id=%s',(part_id,))
    return calculate(cur.fetchone(),demand,exclude_pending)

@router.post('/requests')
def create_request(payload:PurchaseRequest):
    with transaction() as cur:
        replay=once(cur,payload,'request')
        if replay is not None:return replay
        row=eligible_suggestion(cur,payload.part_id,payload.demand)
        if row['pending_quantity']:fail('该物料已有待审批申请，请先处理原申请')
        if not payload.manual and row['suggested'] is None:fail('库存或预警值未知，请先维护库存和主数据')
        if not payload.manual and payload.quantity>row['suggested']:fail('申请数量超过当前补货建议，请刷新或核实额外需求')
        cur.execute('INSERT INTO purchase_request(part_id,supplier_id,quantity,unit_price,expected_date,demand,actor,reason) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',(payload.part_id,row['supplier_id'],payload.quantity,payload.unit_price,payload.expected_date,payload.demand,payload.actor,payload.reason))
        request_id=cur.lastrowid
        if payload.manual:cur.execute('INSERT INTO purchase_request_manual(request_id) VALUES(%s)',(request_id,))
        return finish(cur,payload.request_id,{'id':request_id,'status':'pending'})

@router.get('/requests')
def requests(current:int=Query(1,ge=1),size:int=Query(20,ge=1,le=100)):
    return {'records':read('SELECT r.*,p.name,p.part_code,s.name supplier_name FROM purchase_request r LEFT JOIN part p ON p.id=r.part_id LEFT JOIN supplier s ON s.id=r.supplier_id ORDER BY r.id DESC LIMIT %s OFFSET %s',(size,(current-1)*size)), 'total':read('SELECT COUNT(*) n FROM purchase_request',one=True)['n']}

@router.post('/requests/{item_id}/review')
def review_request(item_id:int,payload:Review):
    with transaction() as cur:
        row,replay=review_record(cur,'purchase_request',item_id,payload)
        if replay:return {'id':item_id,'status':row['status'],'order_id':row['order_id']}
        order_id=None
        if payload.decision=='approve':
            suggestion=eligible_suggestion(cur,row['part_id'],row['demand'],row['quantity'])
            if row['supplier_id']!=suggestion['supplier_id']:fail('供应商归属已变化，请重新申请')
            if row['expected_date']<date.today():fail('交货日期已过期，请重新申请')
            cur.execute('SELECT request_id FROM purchase_request_manual WHERE request_id=%s',(item_id,))
            manual=bool(cur.fetchone())
            if not manual and (suggestion['suggested'] is None or row['quantity']>suggestion['suggested']):fail('库存或在途订单已变化，申请数量超过当前需求，请重新申请')
            owner = re.match(r'^账户#(\d+) ', row['actor'])
            cur.execute('INSERT INTO purchase_order(order_number,total_amount,status,expected_delivery_date,remark,created_by) VALUES(%s,%s,2,%s,%s,%s)',('PR'+str(item_id)+'-'+uuid4().hex[:12],row['quantity']*row['unit_price'],row['expected_date'],f"采购申请 #{item_id}，审批人 {payload.actor}",int(owner.group(1)) if owner else None))
            order_id=cur.lastrowid
            cur.execute('INSERT INTO order_detail(order_id,part_id,quantity,unit_price) VALUES(%s,%s,%s,%s)',(order_id,row['part_id'],row['quantity'],row['unit_price']))
            cur.execute('UPDATE purchase_request SET order_id=%s WHERE id=%s',(order_id,item_id))
        mark_review(cur,'purchase_request',item_id,payload)
        return {'id':item_id,'status':'approved' if payload.decision=='approve' else 'rejected','order_id':order_id}

@router.get('/receiving')
def receiving_list(q:str=Query('',max_length=100),current:int=Query(1,ge=1),size:int=Query(20,ge=1,le=100)):
    storage_timezone = receipt_storage_timezone()
    keyword=f'%{q.strip()}%'
    # Match at order level so multiple matching lines neither duplicate rows nor inflate totals.
    where='''WHERE o.deleted=0 AND (o.order_number LIKE %s OR EXISTS (
        SELECT 1 FROM order_detail sd JOIN part sp ON sp.id=sd.part_id
        WHERE sd.order_id=o.id AND sd.deleted=0 AND (sp.name LIKE %s OR sp.part_code LIKE %s)))'''
    params=(keyword,keyword,keyword)
    rows=read('''SELECT o.id,o.order_number,o.status,o.expected_delivery_date,
    CASE WHEN o.status=4 THEN 'complete' WHEN COALESCE(r.accepted,0)>0 THEN 'partial' ELSE 'ordered' END receipt_state,
    COALESCE(r.accepted,0) accepted,COALESCE(r.rejected,0) rejected
    FROM purchase_order o LEFT JOIN (SELECT g.order_id,SUM(l.accepted) accepted,SUM(l.rejected) rejected
    FROM goods_receipt g JOIN goods_receipt_line l ON l.receipt_id=g.id GROUP BY g.order_id) r ON r.order_id=o.id '''+where+' ORDER BY o.id DESC LIMIT %s OFFSET %s',(*params,size,(current-1)*size))
    if rows:
        ids=[row['id'] for row in rows]
        marks=','.join(['%s']*len(ids))
        items=receipt_read('SELECT d.id AS detail_id,d.order_id,d.part_id,p.name,p.part_code,p.model,p.unit,s.name supplier_name FROM order_detail d LEFT JOIN part p ON p.id=d.part_id LEFT JOIN supplier s ON s.id=p.supplier_id WHERE d.deleted=0 AND d.order_id IN ('+marks+') ORDER BY d.order_id,d.id',tuple(ids))
        batches=receipt_read('''SELECT g.order_id,l.detail_id,g.id AS receipt_id,g.delivery_number,
            l.accepted AS accepted_quantity,g.created_at AS received_at
            FROM goods_receipt g JOIN goods_receipt_line l ON l.receipt_id=g.id
            WHERE l.accepted>0 AND g.order_id IN ('''+marks+') ORDER BY g.created_at,g.id,l.id',tuple(ids))
        by_detail={}
        for batch in batches:
            batch['received_at']=receipt_time(batch['received_at'], storage_timezone)
            by_detail.setdefault(batch['detail_id'],[]).append(batch)
        grouped={order_id:[] for order_id in ids}
        for item in items:
            item['accepted_batches']=by_detail.get(item['detail_id'],[])
            grouped[item['order_id']].append(item)
        for row in rows:
            row['receipt_items']=grouped[row['id']]
            for field,source in [('part_names','name'),('models','model'),('supplier_names','supplier_name')]:
                row[field]='\n'.join(dict.fromkeys(item[source] for item in grouped[row['id']] if item.get(source)))
    return {'records':rows,'total':read('SELECT COUNT(*) n FROM purchase_order o '+where,params,True)['n']}

class OrderReview(Review):
    expected_version:str=Field(min_length=64,max_length=64)

@router.post('/receiving/{order_id}/review')
def review_existing_order(order_id:int,payload:OrderReview):
    """Audited review entry for legacy/manual pending purchase orders."""
    from backend.app import _ensure_order_supplier
    with transaction() as cur:
        cur.execute('SELECT * FROM purchase_order WHERE id=%s AND deleted=0 FOR UPDATE',(order_id,))
        order=cur.fetchone()
        if not order:fail('订单不存在',404)
        user = current_identity.get()
        if user and order.get('created_by') == user['id']:
            fail('不能审批本人创建的订单，请由其他审批人处理',403)
        cur.execute('SELECT * FROM purchase_order_review WHERE order_id=%s',(order_id,))
        existing=cur.fetchone()
        if existing:
            if (existing['decision'],existing['actor'],existing['reason'])!=(payload.decision,payload.actor,payload.reason):fail('订单已审核')
            return {'id':order_id,'status':2 if payload.decision=='approve' else 5}
        if order['status']!=1:fail('仅待审核订单需要审核')
        if payload.expected_version!=order_version(order,receipt_lines(cur,order_id)):fail('订单内容已变化，请刷新明细后重新审核')
        if payload.decision=='approve':
            cur.execute('SELECT d.part_id,p.supplier_id FROM order_detail d LEFT JOIN part p ON p.id=d.part_id WHERE d.order_id=%s AND d.deleted=0',(order_id,))
            lines=cur.fetchall();suppliers={r['supplier_id'] for r in lines}
            if not lines or len(suppliers)!=1 or None in suppliers:fail('订单明细缺失或供应商不一致')
            _ensure_order_supplier(cur,next(iter(suppliers)),{r['part_id'] for r in lines})
        cur.execute('INSERT INTO purchase_order_review(order_id,decision,actor,reason) VALUES(%s,%s,%s,%s)',(order_id,payload.decision,payload.actor,payload.reason))
        cur.execute('UPDATE purchase_order SET status=%s WHERE id=%s',(2 if payload.decision=='approve' else 5,order_id))
        return {'id':order_id,'status':2 if payload.decision=='approve' else 5}


class StartPurchase(Input):
    actor:str=Field(min_length=1,max_length=100)

@router.post('/receiving/{order_id}/start')
def start_purchase(order_id:int,payload:StartPurchase):
    """Narrow procurement-only entry; reuse the same transactional update as MCP."""
    from backend.app import order_update, OrderUpdate
    return order_update(order_id, OrderUpdate(status=3))
