"""Offline SQL coverage for actual receipt evidence and Beijing day boundaries."""
import sqlite3
from datetime import datetime, timezone, timedelta

import pymysql
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend import operations


@pytest.fixture
def receipts(monkeypatch):
    db = sqlite3.connect(':memory:', check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.executescript('''
      CREATE TABLE supplier(id INTEGER, name TEXT, supplier_code TEXT);
      CREATE TABLE part(id INTEGER, name TEXT, part_code TEXT, model TEXT,
        specification TEXT, category TEXT, unit TEXT, supplier_id INTEGER);
      CREATE TABLE purchase_order(id INTEGER, order_number TEXT, status INTEGER,
        expected_delivery_date TEXT, deleted INTEGER);
      CREATE TABLE order_detail(id INTEGER, order_id INTEGER, part_id INTEGER, deleted INTEGER);
      CREATE TABLE goods_receipt(id INTEGER, order_id INTEGER, delivery_number TEXT, created_at TEXT);
      CREATE TABLE goods_receipt_line(id INTEGER, receipt_id INTEGER, detail_id INTEGER,
        part_id INTEGER, accepted INTEGER, rejected INTEGER);
      INSERT INTO supplier VALUES(1,'真实供应商','S1');
      INSERT INTO part VALUES(1,'物料甲','P1','M1','规格甲','配件','个',1),(2,'物料乙','P2','M2',NULL,NULL,'箱',1);
      INSERT INTO purchase_order VALUES(1,'PO1',3,'1999-01-01',0),(2,'PO2',4,'1998-01-01',0);
      INSERT INTO order_detail VALUES(11,1,1,0),(12,1,2,0),(21,2,1,0);
      INSERT INTO goods_receipt VALUES(1,1,'BEFORE','2026-10-07 23:59:59'),
        (2,1,'MIDNIGHT','2026-10-08 00:00:00'),(3,1,'BATCH2','2026-10-08 12:30:00'),
        (4,1,'REJECTED','2026-10-08 16:00:00'),(5,2,'LAST','2026-10-08 23:59:59'),
        (6,2,'NEXT','2026-10-09 00:00:00');
      INSERT INTO goods_receipt_line VALUES(1,1,11,1,99,0),(2,2,11,1,3,2),
        (3,3,11,1,4,0),(4,3,12,2,2,1),(5,4,12,2,0,9),(6,5,21,1,1,0),(7,6,21,1,88,0);
    ''')
    def read(sql, params=(), one=False):
        if 'utc_offset' in sql:
            return {'utc_offset': 28800}
        rows = [dict(row) for row in db.execute(sql.replace('%s','?'), params or ())]
        return (rows[0] if rows else None) if one else rows
    monkeypatch.setattr(operations, 'read', read)
    app = FastAPI()
    app.include_router(operations.router)
    with TestClient(app) as client:
        yield client, db
    db.close()


def test_daily_receipts_include_only_accepted_rows_in_half_open_day(receipts):
    client, _ = receipts
    response = client.get('/api/operations/receipts/daily?date=2026-10-08')
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['date'] == '2026-10-08' and data['timezone'] == 'Asia/Shanghai'
    assert data['start_at'] == '2026-10-08T00:00:00+08:00'
    assert data['end_at'] == '2026-10-09T00:00:00+08:00'
    assert data['total'] == len(data['records']) == 4
    assert [r['receipt_line_id'] for r in data['records']] == [2,3,4,6]
    row = data['records'][0]
    assert {k:row[k] for k in ['part_name','part_code','model','supplier_name','supplier_code','unit','accepted','received_at','order_number','delivery_number']} == {
        'part_name':'物料甲','part_code':'P1','model':'M1','supplier_name':'真实供应商','supplier_code':'S1',
        'unit':'个','accepted':3,'received_at':'2026-10-08 00:00:00','order_number':'PO1','delivery_number':'MIDNIGHT'}
    assert data['totals'] == {'accepted_quantity':10,'receipt_count':3,'order_count':2,'part_count':2,
        'by_unit':[{'unit':'个','accepted_quantity':8},{'unit':'箱','accepted_quantity':2}]}


def test_empty_day_has_explicit_zero_totals(receipts):
    data = receipts[0].get('/api/operations/receipts/daily?date=2026-10-01').json()
    assert data['records'] == [] and data['total'] == 0
    assert data['totals']['accepted_quantity'] == 0


@pytest.mark.parametrize('value',['2026-02-30','20261008','2026-1-8','2026-10-08T00:00:00','9999-12-31'])
def test_daily_date_rejects_invalid_or_non_date_values(receipts,value):
    assert receipts[0].get('/api/operations/receipts/daily', params={'date':value}).status_code == 422


def test_default_date_uses_beijing_even_before_utc_midnight(receipts,monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026,10,7,17,30,tzinfo=timezone.utc).astimezone(tz)
    monkeypatch.setattr(operations,'datetime',Clock,raising=False)
    assert receipts[0].get('/api/operations/receipts/daily').json()['date'] == '2026-10-08'


def test_database_failure_is_not_an_empty_day(receipts,monkeypatch):
    def unavailable(*args,**kwargs):
        raise pymysql.OperationalError(2003,'offline')
    monkeypatch.setattr(operations,'read',unavailable)
    response = receipts[0].get('/api/operations/receipts/daily?date=2026-10-08')
    assert response.status_code == 503
    assert 'records' not in response.json() and '数据库' in response.json()['detail']


def test_unverified_storage_timezone_does_not_report_the_wrong_day(receipts,monkeypatch):
    monkeypatch.setattr(operations,'read',lambda *args,**kwargs:{'utc_offset':None})
    response = receipts[0].get('/api/operations/receipts/daily?date=2026-10-08')
    assert response.status_code == 503 and '时区' in response.json()['detail']


def test_utc_database_uses_beijing_boundaries_and_displays_beijing_times(receipts,monkeypatch):
    client,db=receipts
    db.execute("UPDATE goods_receipt SET created_at=datetime(created_at,'-8 hours')")
    original=operations.read
    def read(sql,params=(),one=False):
        if 'utc_offset' in sql:return {'utc_offset':0}
        return original(sql,params,one)
    monkeypatch.setattr(operations,'read',read)
    response=client.get('/api/operations/receipts/daily?date=2026-10-08')
    assert response.status_code==200,response.text
    data=response.json()
    assert [r['receipt_line_id'] for r in data['records']]==[2,3,4,6]
    assert data['records'][0]['received_at']=='2026-10-08 00:00:00'
    rows=client.get('/api/operations/receiving').json()['records']
    order=next(row for row in rows if row['id']==1)
    assert order['receipt_items'][0]['accepted_batches'][1]['received_at']=='2026-10-08 00:00:00'


def test_receiving_keeps_material_batch_times_and_no_date_fallback(receipts):
    client, db = receipts
    db.execute("INSERT INTO purchase_order VALUES(3,'LEGACY',4,'1997-01-01',0)")
    db.execute('INSERT INTO order_detail VALUES(31,3,2,0)')
    rows = client.get('/api/operations/receiving').json()['records']
    assert rows[0]['receipt_items'][0]['accepted_batches'] == []
    order = next(row for row in rows if row['id']==1)
    items = order['receipt_items']
    assert len(items) == 2
    assert [batch['delivery_number'] for batch in items[0]['accepted_batches']] == ['BEFORE','MIDNIGHT','BATCH2']
    assert items[0]['accepted_batches'][1]['received_at'] == '2026-10-08 00:00:00'
    assert [batch['delivery_number'] for batch in items[1]['accepted_batches']] == ['BATCH2']
