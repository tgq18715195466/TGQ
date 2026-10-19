"""Real order validation in a fresh disposable database only."""
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

@pytest.mark.skipif(os.getenv("ORDER_TEST_MYSQL")!="1",reason="opt-in isolated MySQL test")
def test_real_admission_and_field_checks(monkeypatch):
    name="order_test_"+uuid4().hex
    config={k:v for k,v in backend.MYSQL_CONFIG.items() if k!="database"}
    admin=pymysql.connect(**config,autocommit=True,connect_timeout=3)
    with admin.cursor() as cur: cur.execute(f"CREATE DATABASE `{name}`")
    def connect(): return pymysql.connect(**config,database=name,cursorclass=pymysql.cursors.DictCursor)
    try:
        conn=connect()
        with conn.cursor() as cur:
            for sql in (Path(__file__).resolve().parents[2]/"deploy/mysql/001-schema.sql").read_text(encoding="utf-8").split(";"):
                if sql.strip(): cur.execute(sql)
            cur.execute("INSERT INTO supplier(id,supplier_code,name,status) VALUES(1,'SYNTH-1','Synthetic',1),(2,'SYNTH-2','Synthetic blocked',3)")
            cur.execute("INSERT INTO part(id,part_code,name,supplier_id) VALUES(1,'P-1','Synthetic part',1),(2,'P-2','Synthetic part',2)")
        conn.commit(); __import__("backend.migrate",fromlist=["migrate"]).migrate(conn); conn.close()
        monkeypatch.setattr(backend,"_connect",connect)
        with TestClient(_transaction_app) as client:
            good={"supplierId":1,"expectedDeliveryDate":"2099-01-01","orderNumber":"TEST-1","orderDetail":[{"partId":1,"quantity":2,"unitPrice":10}]}
            assert client.post('/api/orders/create',json={"orderDetail":good['orderDetail']}).status_code==422
            assert client.post('/api/orders/create',json={**good,"supplierId":2}).status_code==409
            assert client.post('/api/orders/create',json={**good,"orderDetail":[{"partId":2,"quantity":2,"unitPrice":10}]}).status_code==422
            assert client.post('/api/orders/create',json=good).status_code==201
            conn=connect()
            with conn.cursor() as cur: cur.execute('UPDATE supplier SET status=3 WHERE id=1')
            conn.commit(); conn.close()
            blocked=client.post('/api/orders/create',json={**good,"orderNumber":"TEST-2"})
            assert blocked.status_code==409 and blocked.json()['detail']['code']=='SUPPLIER_NOT_ACTIVE'
            conn=connect()
            with conn.cursor() as cur:
                cur.execute('SELECT COUNT(*) AS n FROM purchase_order')
                assert cur.fetchone()['n']==1
                cur.execute('SELECT COUNT(*) AS n FROM order_detail')
                assert cur.fetchone()['n']==1
            conn.close()
    finally:
        assert name.startswith('order_test_') and len(name)==43
        with admin.cursor() as cur: cur.execute(f'DROP DATABASE `{name}`')
        admin.close()
