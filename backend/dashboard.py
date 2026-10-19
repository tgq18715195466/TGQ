"""Read-only dashboard endpoints using the existing ERP database."""
from fastapi import APIRouter, Query, HTTPException
from typing import Literal

router = APIRouter(prefix="/api/dashboard", tags=["数据看板"])
INVENTORY = "LEFT JOIN (SELECT part_id, SUM(current_quantity) AS quantity FROM inventory WHERE deleted=0 GROUP BY part_id) i ON i.part_id=p.id"
STOCK_STATE = "CASE WHEN i.quantity IS NULL THEN 'unrecorded' WHEN i.quantity<=0 THEN 'out' WHEN p.stock_warning_value IS NULL OR p.stock_warning_value<=0 THEN 'unconfigured' WHEN i.quantity<p.stock_warning_value THEN 'low' ELSE 'normal' END"


def read(sql, params=(), one=False):
    from backend.app import _connect, _jsonable
    conn=_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params if params else None)
            rows=[cur.fetchone()] if one else cur.fetchall()
            result=[{k:_jsonable(v) for k,v in row.items()} for row in rows if row]
            return (result[0] if result else None) if one else result
    finally: conn.close()


def page_result(sql, where, params, current, size, order):
    condition=" AND ".join(where)
    total=read(f"SELECT COUNT(*) AS n FROM ({sql} WHERE {condition}) count_rows", params,True)['n']
    records=read(f"{sql} WHERE {condition} ORDER BY {order} LIMIT %s OFFSET %s",[*params,size,(current-1)*size])
    return {"records":records,"total":total,"current":current,"size":size}


@router.get('/summary')
def summary():
    from backend.auth import current_identity, can
    user = current_identity.get()
    if user and not can(user, 'read_procurement'):
        stock=read(f"SELECT stock_state,COUNT(*) AS count FROM (SELECT {STOCK_STATE} AS stock_state FROM part p {INVENTORY} WHERE p.deleted=0) states GROUP BY stock_state")
        return {"stock":stock,"suppliers":{},"orders":{},"supplier_distribution":[],"purchase_trend":[]}
    suppliers=read("SELECT COUNT(*) AS total, COALESCE(SUM(status=1),0) AS active FROM supplier WHERE deleted=0",one=True)
    stock=read(f"SELECT stock_state,COUNT(*) AS count FROM (SELECT {STOCK_STATE} AS stock_state FROM part p {INVENTORY} WHERE p.deleted=0) states GROUP BY stock_state")
    orders=read("SELECT COUNT(*) AS total,COALESCE(SUM(status=1),0) AS pending,COALESCE(SUM(status IN (2,3)),0) AS purchasing,COALESCE(SUM(status IN (1,2,3) AND expected_delivery_date<CURDATE()),0) AS overdue,COALESCE(SUM(CASE WHEN status<>5 AND order_time>=DATE_FORMAT(CURDATE(),'%Y-%m-01') AND order_time<DATE_ADD(LAST_DAY(CURDATE()),INTERVAL 1 DAY) THEN total_amount ELSE 0 END),0) AS month_amount FROM purchase_order WHERE deleted=0",one=True)
    distribution=read("SELECT status,COUNT(*) AS count FROM supplier WHERE deleted=0 GROUP BY status ORDER BY status")
    trend=read("SELECT DATE_FORMAT(order_time,'%Y-%m') AS month,SUM(total_amount) AS amount FROM purchase_order WHERE deleted=0 AND status<>5 AND order_time>=DATE_FORMAT(DATE_SUB(CURDATE(),INTERVAL 5 MONTH),'%Y-%m-01') AND order_time<NOW() GROUP BY month ORDER BY month")
    return {"suppliers":suppliers,"stock":stock,"orders":orders,"supplier_distribution":distribution,"purchase_trend":trend}


@router.get('/suppliers')
def suppliers(q:str=Query('',max_length=100),status:int|None=Query(None,ge=1,le=3),current:int=Query(1,ge=1),size:int=Query(20,ge=1,le=100)):
    where=['deleted=0']; params=[]
    if q: where.append('(name LIKE %s OR supplier_code LIKE %s)'); params.extend([f'%{q}%']*2)
    if status is not None: where.append('status=%s'); params.append(status)
    return page_result('SELECT id,supplier_code,name,contact_person,phone,email,address,credit_rating,status FROM supplier',where,params,current,size,'id')


@router.get('/inventory')
def inventory(q:str=Query('',max_length=100),state:Literal['all','warning','out','low','normal','unrecorded','unconfigured']='all',current:int=Query(1,ge=1),size:int=Query(20,ge=1,le=100)):
    sql=f"SELECT p.id,p.part_code,p.name,p.model,p.specification,p.unit,p.category,p.purchase_price,p.supplier_id,s.name AS supplier_name,s.status AS supplier_status,i.quantity,p.stock_warning_value,CASE WHEN i.quantity IS NULL OR p.stock_warning_value IS NULL OR p.stock_warning_value<=0 THEN NULL ELSE GREATEST(p.stock_warning_value-i.quantity,0) END AS shortage,{STOCK_STATE} AS stock_state FROM part p {INVENTORY} LEFT JOIN supplier s ON s.id=p.supplier_id AND s.deleted=0"
    where=['p.deleted=0']; params=[]
    if q: where.append('(p.name LIKE %s OR p.part_code LIKE %s OR p.model LIKE %s)'); params.extend([f'%{q}%']*3)
    if state=='warning': where.append(f"({STOCK_STATE}) IN ('out','low')")
    elif state!='all': where.append(f'({STOCK_STATE})=%s'); params.append(state)
    return page_result(sql,where,params,current,size,'p.id')


@router.get('/orders')
def orders(q:str=Query('',max_length=100),status:int|None=Query(None,ge=1,le=5),overdue:bool=False,purchasing:bool=False,month:bool=False,current:int=Query(1,ge=1),size:int=Query(20,ge=1,le=100)):
    where=['deleted=0']; params=[]
    if q: where.append('order_number LIKE %s'); params.append(f'%{q}%')
    if status is not None: where.append('status=%s'); params.append(status)
    if purchasing: where.append('status IN (2,3)')
    if month: where.append('status<>5 AND order_time>=DATE_SUB(CURDATE(),INTERVAL (DAYOFMONTH(CURDATE())-1) DAY) AND order_time<DATE_ADD(LAST_DAY(CURDATE()),INTERVAL 1 DAY)')
    if overdue: where.append('status IN (1,2,3) AND expected_delivery_date<CURDATE()')
    result=page_result("SELECT id,created_by,order_number,total_amount,status,order_time,expected_delivery_date,actual_delivery_date,remark,CASE WHEN status IN (1,2,3) AND expected_delivery_date<CURDATE() THEN DATEDIFF(CURDATE(),expected_delivery_date) ELSE 0 END AS overdue_days FROM purchase_order",where,params,current,size,'id DESC')
    ids=[row['id'] for row in result['records']]
    if ids:
        details=read('SELECT d.order_id,p.name part_name,s.name supplier_name FROM order_detail d LEFT JOIN part p ON p.id=d.part_id LEFT JOIN supplier s ON s.id=p.supplier_id WHERE d.deleted=0 AND d.order_id IN ('+','.join(['%s']*len(ids))+') ORDER BY d.order_id,d.id',tuple(ids))
        grouped={order_id:[] for order_id in ids}
        for item in details:grouped[item['order_id']].append(item)
        for row in result['records']:
            for field,source in [('part_names','part_name'),('supplier_names','supplier_name')]:
                row[field]='\n'.join(dict.fromkeys(item[source] for item in grouped[row['id']] if item.get(source)))
    return result



@router.get('/orders/{order_id}')
def order_detail(order_id:int):
    item=read('SELECT id,created_by,order_number,total_amount,status,order_time,expected_delivery_date,actual_delivery_date,remark FROM purchase_order WHERE id=%s AND deleted=0',(order_id,),True)
    if item is None: raise HTTPException(404,'订单不存在')
    item['purchase_execution']=read('SELECT actor,started_at FROM purchase_order_execution WHERE order_id=%s',(order_id,),True)
    item['details']=read('SELECT d.id,d.part_id,d.quantity,d.unit_price,d.quantity*d.unit_price AS subtotal,d.remark,p.part_code,p.name AS part_name,s.name AS supplier_name FROM order_detail d LEFT JOIN part p ON p.id=d.part_id LEFT JOIN supplier s ON s.id=p.supplier_id WHERE d.order_id=%s AND d.deleted=0 ORDER BY d.id',(order_id,))
    return item
