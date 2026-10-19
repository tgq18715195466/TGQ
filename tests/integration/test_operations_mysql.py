import os
from pathlib import Path
from uuid import uuid4
import pymysql
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend import app as backend


# Transaction/validation tests deliberately exercise routers without authentication.
# Production middleware stays installed; real permissions have their own MySQL tests.
_transaction_app = FastAPI()
_transaction_app.include_router(backend.router)
_transaction_app.include_router(backend.dashboard_router)
_transaction_app.include_router(backend.operations_router)
from backend.migrate import migrate

@pytest.fixture
def ops(monkeypatch):
    if os.getenv('ORDER_TEST_MYSQL')!='1':pytest.skip('isolated MySQL opt-in')
    name='ops_test_'+uuid4().hex
    cfg={k:v for k,v in backend.MYSQL_CONFIG.items() if k!='database'}
    admin=pymysql.connect(**cfg,autocommit=True)
    with admin.cursor() as c:c.execute(f'CREATE DATABASE `{name}`')
    def connect():return pymysql.connect(**cfg,database=name,cursorclass=pymysql.cursors.DictCursor)
    try:
        conn=connect()
        with conn.cursor() as c:
            for sql in Path('deploy/mysql/001-schema.sql').read_text(encoding='utf-8').split(';'):
                if sql.strip():c.execute(sql)
            c.execute("INSERT INTO supplier(id,supplier_code,name,status) VALUES(1,'S1','Synthetic',1)")
            c.execute("INSERT INTO part(id,part_code,name,supplier_id,stock_warning_value,purchase_price) VALUES(1,'P1','Synthetic',1,20,2),(2,'P2','No stock',1,10,2)")
            c.execute('INSERT INTO inventory(part_id,current_quantity) VALUES(1,2)')
            c.execute("INSERT INTO purchase_order(id,order_number,status,total_amount) VALUES(1,'PO-TEST',2,20)")
            c.execute('INSERT INTO order_detail(id,order_id,part_id,quantity,unit_price) VALUES(1,1,1,10,2)')
        conn.commit();migrate(conn);conn.close()
        monkeypatch.setattr(backend,'_connect',connect)
        with TestClient(_transaction_app) as client:yield client,connect
    finally:
        assert name.startswith('ops_test_') and len(name)==41
        with admin.cursor() as c:c.execute(f'DROP DATABASE `{name}`')
        admin.close()

def post(c,path,data):
    r=c.post('/api/operations/'+path,json=data)
    assert r.status_code==200,r.text
    return r.json()

def test_receiving_keyword_matches_materials_without_duplicate_orders(ops):
    client,connect=ops
    conn=connect()
    with conn.cursor() as cur:
        cur.execute("UPDATE part SET name='前减震器',part_code='FC3001' WHERE id=1")
        cur.execute("UPDATE part SET name='前轮轴承',part_code='FC3002' WHERE id=2")
        cur.execute("INSERT INTO part(id,part_code,name,supplier_id) VALUES(3,'PC3003','后轮轴承',1)")
        cur.execute("INSERT INTO purchase_order(id,order_number,status,total_amount,deleted) VALUES(2,'PO-SECOND',2,20,0),(3,'PO-DELETED-LINE',2,20,0),(4,'PO-DELETED',2,20,1)")
        cur.execute('INSERT INTO order_detail(order_id,part_id,quantity,unit_price,deleted) VALUES(1,2,1,2,0),(1,3,1,2,0),(2,1,1,2,0),(3,1,1,2,1),(4,1,1,2,0)')
    conn.commit();conn.close()
    def search(q,**params):
        response=client.get('/api/operations/receiving',params={'q':q,**params})
        assert response.status_code==200,response.text
        return response.json()
    for keyword in ('前','减震','FC3001','  前  '):
        result=search(keyword)
        assert result['total']==2
        assert [row['id'] for row in result['records']]==[2,1]
    first=search('前',size=1,current=1)
    second=search('前',size=1,current=2)
    assert first['total']==second['total']==2
    assert [row['id'] for row in first['records']]==[2]
    assert [row['id'] for row in second['records']]==[1]
    assert len(second['records'][0]['receipt_items'])==3
    assert '后轮轴承' in second['records'][0]['part_names']
    assert search('PO-TEST')['records'][0]['id']==1
    assert search('不存在')['total']==0
    assert search('')['total']==3


def test_receipt_and_stock(ops):
    c,conn=ops
    payload=dict(request_id='receipt-1',delivery_number='DN1',actor='Warehouse',reason='batch one',order_id=1,lines=[dict(detail_id=1,accepted=4,rejected=2)])
    a=post(c,'receipts',payload);assert post(c,'receipts',payload)==a
    assert c.post('/api/operations/receipts',json={**payload,'reason':'changed'}).status_code==409
    d=c.get('/api/operations/receiving/1').json();assert d['receipt_state']=='partial' and d['lines'][0]['remaining']==6
    assert c.post('/api/operations/receipts',json={**payload,'request_id':'bad','lines':[dict(detail_id=1,accepted=7,rejected=0)]}).status_code==409
    post(c,'receipts',{**payload,'request_id':'receipt-2','delivery_number':'DN2','lines':[dict(detail_id=1,accepted=6,rejected=0)]})
    assert c.get('/api/operations/receiving/1').json()['receipt_state']=='complete'
    stock=dict(request_id='issue-1',actor='Warehouse',reason='production',part_id=1,kind='issue',quantity=3,document='ISSUE-1',recipient_name='测试领料人')
    post(c,'stock',stock);post(c,'stock',stock)
    post(c,'stock',{**stock,'request_id':'return-1','kind':'return','quantity':1})
    assert c.post('/api/operations/stock',json={**stock,'request_id':'bad-issue','quantity':100}).status_code==409
    assert c.post('/api/operations/stock',json={**stock,'request_id':'bad-count','kind':'count','quantity':5,'expected_quantity':0}).status_code==409
    post(c,'stock',{**stock,'request_id':'count-1','kind':'count','quantity':8,'expected_quantity':10})
    movements=c.get('/api/operations/movements').json()['records']
    assert len(movements)==5 and movements[0]['after_quantity']==8
    assert c.put('/api/orders/update/1',json={'status':2}).status_code==409

def test_issue_recipient_is_required_persisted_and_idempotent(ops):
    c,connect=ops
    body=dict(request_id='named-issue',actor='Warehouse',reason='维修领料',part_id=1,kind='issue',quantity=1,document='ISSUE-NAMED')
    assert c.post('/api/operations/stock',json=body).status_code==422
    assert c.post('/api/operations/stock',json={**body,'recipient_name':'   '}).status_code==422
    assert c.get('/api/operations/movements').json()['total']==0
    named={**body,'recipient_name':'  张三  '}
    result=post(c,'stock',named)
    assert post(c,'stock',named)==result
    assert c.post('/api/operations/stock',json={**named,'recipient_name':'李四'}).status_code==409
    rows=c.get('/api/operations/movements').json()['records']
    assert len(rows)==1 and rows[0]['recipient_name']=='张三'
    assert rows[0]['actor']=='Warehouse' and rows[0]['after_quantity']==1
    post(c,'stock',{**body,'request_id':'named-return','kind':'return'})
    rows=c.get('/api/operations/movements').json()['records']
    assert rows[0]['recipient_name'] is None
    # Re-running the additive migration must preserve both stock and the name.
    conn=connect()
    try:migrate(conn);migrate(conn)
    finally:conn.close()
    rows=c.get('/api/operations/movements').json()['records']
    assert rows[1]['recipient_name']=='张三'

def test_master_and_replenishment(ops):
    c,conn=ops
    proposal=dict(request_id='change-1',actor='Buyer',reason='change status',entity='supplier',entity_id=1,values={'supplier_code':'S1','name':'Synthetic','status':3})
    p=post(c,'master-changes',proposal)
    assert c.post(f"/api/operations/master-changes/{p['id']}/review",json={'actor':'Buyer','decision':'approve','reason':'ok'}).status_code==409
    post(c,f"master-changes/{p['id']}/review",dict(actor='Manager',decision='approve',reason='approved'))
    post(c,f"master-changes/{p['id']}/review",dict(actor='Manager',decision='approve',reason='approved'))
    assert c.get('/api/dashboard/suppliers').json()['records'][0]['status']==3
    p2=post(c,'master-changes',{**proposal,'request_id':'change-2','values':{**proposal['values'],'status':1}})
    post(c,f"master-changes/{p2['id']}/review",dict(actor='Manager',decision='approve',reason='reactivate'))
    suggestion=c.get('/api/operations/suggestions?part_id=1&demand=0').json()['records'][0]
    assert suggestion['on_order']==10 and suggestion['suggested']==8
    req=dict(request_id='request-1',actor='Buyer',reason='restock',part_id=1,quantity=8,unit_price=2,expected_date='2099-01-01',demand=0)
    p=post(c,'requests',req)
    assert c.post('/api/operations/requests',json={**req,'request_id':'request-2'}).status_code==409
    result=post(c,f"requests/{p['id']}/review",dict(actor='Manager',decision='approve',reason='ok'))
    assert result['order_id']
    assert post(c,f"requests/{p['id']}/review",dict(actor='Manager',decision='approve',reason='ok'))==result
    assert c.get('/api/operations/suggestions?part_id=1').json()['records'][0]['suggested']==0

def test_rejection_only_and_delivery_duplicates(ops):
    c,_=ops
    body=dict(request_id='reject-1',delivery_number='DN-REJECT',actor='Warehouse',reason='quality failed',order_id=1,lines=[dict(detail_id=1,accepted=0,rejected=10)])
    post(c,'receipts',body)
    assert c.get('/api/operations/movements').json()['total']==0
    assert c.get('/api/operations/receiving/1').json()['lines'][0]['remaining']==10
    assert c.post('/api/operations/receipts',json={**body,'request_id':'reject-again'}).status_code==409
    assert c.get('/api/operations/receiving/1').json()['lines'][0]['rejected']==10

def test_master_conflict_new_part_disable_and_legacy_journal(ops):
    c,_=ops
    base=dict(actor='Buyer',reason='synthetic',entity='supplier',entity_id=1,values={'supplier_code':'S1','name':'Updated','status':1})
    a=post(c,'master-changes',{**base,'request_id':'m1'})
    b=post(c,'master-changes',{**base,'request_id':'m2','values':{**base['values'],'name':'Second'}})
    review=dict(actor='Manager',decision='approve',reason='ok')
    post(c,f"master-changes/{a['id']}/review",review)
    assert c.post(f"/api/operations/master-changes/{b['id']}/review",json=review).status_code==409
    post(c,f"master-changes/{b['id']}/review",{**review,'decision':'reject'})
    p=post(c,'master-changes',dict(request_id='newpart',actor='Buyer',reason='new',entity='part',values={'part_code':'P3','name':'New','supplier_id':1,'purchase_price':3.5,'stock_warning_value':10,'enabled':False}))
    approved=post(c,f"master-changes/{p['id']}/review",review)
    assert approved['entity_id']
    r=c.post('/api/orders/create',json={'supplierId':1,'expectedDeliveryDate':'2099-01-01','orderDetail':[{'partId':approved['entity_id'],'quantity':1,'unitPrice':3.5}]})
    assert r.status_code==409
    r=c.post('/api/inventory/adjust',json={'partId':1,'delta':1,'reason':'legacy test','requestId':'legacy-1'});assert r.status_code==200,r.text
    assert c.get('/api/operations/movements').json()['records'][0]['actor']=='legacy-agent'

def test_concurrent_requests_and_receipts(ops):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    c,_=ops
    def parallel(path,bodies):
        barrier=Barrier(len(bodies))
        def send(body):
            with TestClient(_transaction_app) as client:
                barrier.wait(timeout=10)
                return client.post('/api/operations/'+path,json=body)
        with ThreadPoolExecutor(max_workers=len(bodies)) as pool:return list(pool.map(send,bodies))
    body=dict(request_id='same-delivery',delivery_number='DN-CONCURRENT',actor='Warehouse',reason='test',order_id=1,lines=[dict(detail_id=1,accepted=4,rejected=0)])
    results=parallel('receipts',[body]*4)
    assert all(r.status_code==200 for r in results),[r.text for r in results]
    assert c.get('/api/operations/receiving/1').json()['lines'][0]['accepted']==4
    req=dict(actor='Buyer',reason='restock',part_id=1,quantity=8,unit_price=2,expected_date='2099-01-01')
    results=parallel('requests',[{**req,'request_id':'a'},{**req,'request_id':'b'}])
    assert sorted(r.status_code for r in results)==[200,409]
    assert c.get('/api/operations/requests').json()['total']==1

def test_order_review_and_approved_amendment_guards(ops):
    c,connect=ops
    assert c.put('/api/orders/update/1',json={'orderDetail':[{'partId':1,'quantity':999,'unitPrice':2}]}).status_code==409
    conn=connect()
    with conn.cursor() as cur:
        cur.execute("INSERT INTO purchase_order(id,order_number,status,total_amount) VALUES(2,'PENDING',1,6)")
        cur.execute('INSERT INTO order_detail(order_id,part_id,quantity,unit_price) VALUES(2,1,3,2)')
    conn.commit();conn.close()
    assert c.put('/api/orders/update/2',json={'status':2}).status_code==409
    assert c.put('/api/orders/update/2',json={'status':3}).status_code==409
    details=c.get('/api/operations/receiving/2').json()
    review=dict(actor='Manager',decision='approve',reason='verified',expected_version=details['review_version'])
    assert c.post('/api/operations/receiving/2/review',json={**review,'expected_version':'0'*64}).status_code==409
    post(c,'receiving/2/review',review)
    post(c,'receiving/2/review',review)
    assert c.get('/api/operations/receiving/2').json()['review']['actor']=='Manager'
    assert c.put('/api/orders/update/2',json={'totalAmount':999}).status_code==409
    assert c.put('/api/orders/update/2',json={'status':1}).status_code==409

@pytest.mark.parametrize('field',['status','totalAmount'])
def test_null_cannot_bypass_approved_order_guards(ops,field):
    c,_=ops
    assert c.put('/api/orders/update/1',json={field:None}).status_code==422
    assert c.get('/api/operations/receiving/1').json()['status']==2


def test_receiving_material_context_and_delivery_fields(ops):
    c,connect=ops
    conn=connect()
    with conn.cursor() as cur:
        cur.execute("UPDATE part SET model='MODEL-A' WHERE id=1")
    conn.commit();conn.close()
    listing=c.get('/api/operations/receiving').json()['records'][0]
    assert listing['part_names']=='Synthetic'
    assert listing['models']=='MODEL-A'
    assert listing['supplier_names']=='Synthetic'
    detail=c.get('/api/operations/receiving/1').json()
    assert detail['lines'][0]['model']=='MODEL-A'
    assert detail['lines'][0]['supplier_name']=='Synthetic'
    post(c,'receipts',dict(request_id='distinct-fields',delivery_number='DN-20261006',reason='验收四件，拒收两件',actor='Warehouse',order_id=1,lines=[dict(detail_id=1,accepted=4,rejected=2)]))
    saved=c.get('/api/operations/receiving/1').json()['receipts'][0]
    assert saved['delivery_number']=='DN-20261006'
    assert saved['reason']=='验收四件，拒收两件'


def test_manual_replenishment_quantity_overrides_zero_suggestion(ops):
    c,connect=ops
    conn=connect()
    with conn.cursor() as cur:cur.execute('UPDATE inventory SET current_quantity=100 WHERE part_id=1')
    conn.commit();conn.close()
    assert c.get('/api/operations/suggestions?part_id=1').json()['records'][0]['suggested']==0
    body=dict(request_id='manual-1',actor='Buyer',reason='主动确认备货20件',part_id=1,quantity=20,unit_price=2,expected_date='2099-01-01',demand=0,manual=True)
    request=post(c,'requests',body)
    assert post(c,'requests',body)==request
    assert c.post('/api/operations/requests',json={**body,'request_id':'manual-duplicate'}).status_code==409
    result=post(c,f"requests/{request['id']}/review",dict(actor='Manager',decision='approve',reason='确认主动需求'))
    assert result['order_id']
    assert c.get('/api/operations/receiving/'+str(result['order_id'])).json()['lines'][0]['quantity']==20

def test_begin_purchase_is_audited_once_and_does_not_change_stock(ops):
    from backend.auth import current_identity
    c,connect=ops
    token=current_identity.set({'id':7,'role':'purchaser','actor':'账户#7 buyer'})
    try:
        first=c.post('/api/operations/receiving/1/start',json={'actor':'不得使用伪造操作人'})
        assert first.status_code==200,first.text
        assert c.put('/api/orders/update/1',json={'status':3}).status_code==200
    finally:current_identity.reset(token)
    detail=c.get('/api/dashboard/orders/1').json()
    assert detail['status']==3 and detail['purchase_execution']['actor']=='账户#7 buyer'
    assert detail['purchase_execution']['started_at']
    conn=connect()
    with conn.cursor() as cur:
        cur.execute('SELECT COUNT(*) n FROM purchase_order_execution WHERE order_id=1')
        assert cur.fetchone()['n']==1
        cur.execute('SELECT current_quantity FROM inventory WHERE part_id=1')
        assert cur.fetchone()['current_quantity']==2
        cur.execute('UPDATE purchase_order SET status=1 WHERE id=1')
    conn.commit();conn.close()
    assert c.put('/api/orders/update/1',json={'status':3}).status_code==409
