"""Dashboard queries against a disposable MySQL database; never touch ERP records."""
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

@pytest.mark.skipif(os.getenv('ORDER_TEST_MYSQL')!='1',reason='opt-in isolated database')
def test_dashboard_queries(monkeypatch):
    name='dashboard_test_'+uuid4().hex
    config={k:v for k,v in backend.MYSQL_CONFIG.items() if k!='database'}
    admin=pymysql.connect(**config,autocommit=True,connect_timeout=3)
    with admin.cursor() as cur: cur.execute(f'CREATE DATABASE `{name}`')
    def connect(): return pymysql.connect(**config,database=name,cursorclass=pymysql.cursors.DictCursor)
    try:
        conn=connect()
        with conn.cursor() as cur:
            for sql in (Path(__file__).resolve().parents[2]/'deploy/mysql/001-schema.sql').read_text(encoding='utf-8').split(';'):
                if sql.strip(): cur.execute(sql)
            cur.execute("INSERT INTO supplier(id,supplier_code,name,status) VALUES(1,'S1','Synthetic',1),(2,'S2','Blocked',3)")
            for i,threshold in enumerate([10,10,10,10,0,None],1):
                cur.execute('INSERT INTO part(id,part_code,name,supplier_id,stock_warning_value) VALUES(%s,%s,%s,1,%s)',(i,f'P{i}',f'Part {i}',threshold))
            for i,quantity in [(1,0),(2,3),(3,10),(5,20),(6,20)]:
                cur.execute('INSERT INTO inventory(part_id,current_quantity) VALUES(%s,%s)',(i,quantity))
            cur.execute("INSERT INTO purchase_order(id,order_number,status,total_amount,order_time,expected_delivery_date) VALUES(1,'O1',1,100,NOW(),DATE_SUB(CURDATE(),INTERVAL 1 DAY)),(2,'O2',5,999,NOW(),DATE_SUB(CURDATE(),INTERVAL 1 DAY)),(3,'O3',2,200,NOW(),CURDATE()),(4,'O4',4,300,NOW(),CURDATE())")
            cur.execute('INSERT INTO order_detail(order_id,part_id,quantity,unit_price) VALUES(1,1,2,50),(1,2,3,5)')
        conn.commit()
        from backend.migrate import migrate
        migrate(conn);conn.close()
        monkeypatch.setattr(backend,'_connect',connect)
        with TestClient(_transaction_app) as c:
            s=c.get('/api/dashboard/summary'); assert s.status_code==200,s.text
            assert s.json()['orders']==dict(total=4,pending=1,purchasing=1,overdue=1,month_amount=600)
            assert s.json()['suppliers']==dict(total=2,active=1)
            records=c.get('/api/dashboard/inventory').json()['records']
            assert [r['stock_state'] for r in records]==['out','low','normal','unrecorded','unconfigured','unconfigured']
            assert records[1]['shortage']==7 and records[3]['quantity'] is None and records[4]['shortage'] is None
            assert c.get('/api/dashboard/inventory?state=warning').json()['total']==2
            assert c.get('/api/dashboard/suppliers?status=3').json()['records'][0]['id']==2
            assert c.get('/api/dashboard/suppliers?size=1&current=2').json()['records'][0]['id']==2
            assert c.get('/api/dashboard/suppliers',params={'q':"' OR 1=1 --"}).json()['total']==0
            for query,total in [('overdue=true',1),('purchasing=true',1),('month=true',3),('month=true&q=O1',1)]:
                r=c.get('/api/dashboard/orders?'+query);assert r.status_code==200,r.text
                assert r.json()['total']==total
            listing=c.get('/api/dashboard/orders?q=O1').json()
            assert listing['total']==1 and len(listing['records'])==1
            assert listing['records'][0]['part_names']=='Part 1\nPart 2'
            assert listing['records'][0]['supplier_names']=='Synthetic'
            assert c.get('/api/dashboard/orders?q=O2').json()['records'][0]['part_names']==''
            assert c.get('/api/dashboard/orders/1').json()['details'][0]['subtotal']==100
            assert c.get('/api/dashboard/orders/999').status_code==404
            assert c.get('/api/dashboard/inventory?state=invalid').status_code==422
            assert c.get('/api/dashboard/suppliers?current=0').status_code==422
    finally:
        assert name.startswith('dashboard_test_') and len(name)==47
        with admin.cursor() as cur:cur.execute(f'DROP DATABASE `{name}`')
        admin.close()
