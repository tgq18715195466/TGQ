"""Real auth and business transactions in a disposable MySQL database.

Opt in with ORDER_TEST_MYSQL=1. Only the DB connector is replaced: production
middleware, setup/login, password hashing, sessions and permission checks run.
"""
import os
import re
from contextlib import ExitStack, closing
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pymysql
import pytest
from fastapi.testclient import TestClient

from backend import app as backend
from backend.migrate import migrate

pytestmark = pytest.mark.skipif(
    os.getenv('ORDER_TEST_MYSQL') != '1', reason='opt-in isolated MySQL auth test')
PASSWORD = 'Synthetic-Auth-Only-2026!'


def expect(response, status=200):
    assert response.status_code == status, f'{response.request.method} {response.request.url}: {response.text}'
    return response.json()


def order_body(**changes):
    return dict(supplierId=1, orderNumber='AUTH-' + uuid4().hex,
                expectedDeliveryDate='2099-01-01',
                orderDetail=[dict(partId=1, quantity=2, unitPrice=2)],
                createdBy=999999) | changes


def request_body(**changes):
    return dict(request_id=uuid4().hex, actor='Forged requester', reason='synthetic',
                part_id=1, quantity=2, unit_price=2, expected_date='2099-01-01') | changes


def stock_body(**changes):
    return dict(request_id=uuid4().hex, actor='Forged warehouse', reason='synthetic',
                part_id=1, kind='issue', quantity=1, document='AUTH-ISSUE',recipient_name='实际领料人') | changes


@pytest.fixture
def business(monkeypatch):
    name = 'auth_business_test_' + uuid4().hex
    config = {k: v for k, v in backend.MYSQL_CONFIG.items() if k != 'database'}
    admin = pymysql.connect(**config, autocommit=True, connect_timeout=3)
    created = False

    def connect():
        return pymysql.connect(**config, database=name,
                               cursorclass=pymysql.cursors.DictCursor)

    def rows(sql, params=()):
        with closing(connect()) as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    try:
        with admin.cursor() as cur:
            cur.execute(f'CREATE DATABASE `{name}` CHARACTER SET utf8mb4')
            created = True
        with closing(connect()) as conn:
            with conn.cursor() as cur:
                schema = Path(__file__).resolve().parents[2] / 'deploy/mysql/001-schema.sql'
                for sql in schema.read_text(encoding='utf-8').split(';'):
                    if sql.strip():
                        cur.execute(sql)
                cur.execute("INSERT INTO supplier(id,supplier_code,name,status) VALUES(1,'AUTH-S1','Synthetic',1)")
                cur.execute("INSERT INTO part(id,part_code,name,supplier_id,stock_warning_value,purchase_price) VALUES(1,'AUTH-P1','Synthetic',1,100,2)")
                cur.execute('INSERT INTO inventory(part_id,current_quantity) VALUES(1,20)')
                cur.execute("INSERT INTO purchase_order(id,order_number,status,total_amount) VALUES(1,'AUTH-SEED',2,20)")
                cur.execute('INSERT INTO order_detail(id,order_id,part_id,quantity,unit_price) VALUES(1,1,1,10,2)')
            conn.commit()
            migrate(conn)
        monkeypatch.setattr(backend, '_connect', connect)
        with ExitStack() as stack:
            def client():
                return stack.enter_context(TestClient(backend.app, base_url='https://testserver',
                                                      headers={'Origin': 'https://testserver'}))
            administrator = client()
            setup = expect(administrator.post('/api/auth/setup', json=dict(
                username='auth_admin', display_name='Admin', password=PASSWORD)))
            clients, users = {'admin': administrator}, {'admin': setup['user']}
            for username, role in [('purchaser', 'purchaser'), ('warehouse', 'warehouse'),
                                   ('approver', 'approver'), ('other', 'approver')]:
                created_user = expect(administrator.post('/api/auth/users', json=dict(
                    username=username, display_name=username, password=PASSWORD, role=role)))['user']
                clients[username] = client()
                users[username] = expect(clients[username].post('/api/auth/login', json=dict(
                    username=username, password=PASSWORD)))['user']
                assert users[username]['id'] == created_user['id']
                assert users[username]['role'] == role
            yield SimpleNamespace(clients=clients, users=users, rows=rows, client=client)
    finally:
        try:
            if created:
                # Never derive the DROP target from the configured ERP database.
                assert re.fullmatch(r'auth_business_test_[0-9a-f]{32}', name)
                assert name != backend.MYSQL_CONFIG.get('database')
                with admin.cursor() as cur:
                    cur.execute(f'DROP DATABASE `{name}`')
                    cur.execute('SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=%s', (name,))
                    assert cur.fetchone() is None
        finally:
            admin.close()


def test_unauthenticated_and_role_denials_do_not_write(business):
    b = business
    expect(b.client().post('/api/orders/create', json=order_body()), 401)
    purchaser, warehouse = b.clients['purchaser'], b.clients['warehouse']
    receipt = dict(request_id=uuid4().hex, actor='Forged approver', reason='synthetic',
                   delivery_number='AUTH-DN', order_id=1,
                   lines=[dict(detail_id=1, accepted=1, rejected=0)])
    denied_stock = [('/api/operations/stock', stock_body()),
                    ('/api/operations/receipts', receipt),
                    ('/api/inventory/adjust', dict(partId=1, delta=1, reason='synthetic', requestId=uuid4().hex))]
    for path, body in denied_stock:
        expect(purchaser.post(path, json=body, headers={'X-User-Id': str(b.users['admin']['id']),
                                                      'X-Username': 'auth_admin'}), 403)
    expect(warehouse.post('/api/orders/create', json=order_body()), 403)
    expect(warehouse.put('/api/orders/update/1', json={'remark': 'forged'}), 403)
    expect(warehouse.post('/api/operations/requests', json=request_body()), 403)
    expect(warehouse.post('/api/operations/receiving/1/start', json={}), 403)
    for client in (purchaser, warehouse):
        expect(client.post('/api/operations/receiving/1/review', json=dict(
            actor='Admin', decision='approve', reason='synthetic', expected_version='0'*64)), 403)
    assert b.rows('SELECT current_quantity FROM inventory')[0]['current_quantity'] == 20
    assert len(b.rows('SELECT id FROM purchase_order')) == 1
    for table in ('goods_receipt', 'stock_movement', 'inventory_adjustment',
                  'purchase_request', 'purchase_order_review', 'erp_operation'):
        assert b.rows(f'SELECT COUNT(*) n FROM {table}')[0]['n'] == 0


def test_forged_actor_and_order_owner_are_replaced(business):
    b = business
    purchaser, warehouse = b.clients['purchaser'], b.clients['warehouse']
    oid = expect(purchaser.post('/api/orders/create', json=order_body(
        created_by=b.users['admin']['id'])), 201)['data']['id']
    expect(purchaser.put(f'/api/orders/update/{oid}', json=dict(
        createdBy=b.users['admin']['id'], created_by=b.users['admin']['id'], remark='updated')))
    order = b.rows('SELECT created_by,remark FROM purchase_order WHERE id=%s', (oid,))[0]
    assert order == dict(created_by=b.users['purchaser']['id'], remark='updated')
    expect(warehouse.post('/api/operations/stock', json=stock_body(),
                          headers={'X-User-Id': str(b.users['admin']['id']), 'X-Username': 'auth_admin'}))
    assert b.rows('SELECT actor FROM stock_movement')[0]['actor'] == b.users['warehouse']['actor']
    assert expect(warehouse.get('/api/operations/movements'))['records'][0]['recipient_name']=='实际领料人'
    rid = expect(purchaser.post('/api/operations/requests', json=request_body()))['id']
    assert b.rows('SELECT actor FROM purchase_request WHERE id=%s', (rid,))[0]['actor'] == b.users['purchaser']['actor']


@pytest.mark.parametrize('decision', ['approve', 'reject'])
def test_request_self_review_denied_other_approver_succeeds(business, decision):
    b = business
    owner, other = b.clients['approver'], b.clients['other']
    rid = expect(owner.post('/api/operations/requests', json=request_body()))['id']
    review = dict(actor=b.users['other']['actor'], decision=decision, reason='synthetic review')
    # Supplying a different actor must not defeat the self-review check.
    expect(owner.post(f'/api/operations/requests/{rid}/review', json=review), 409)
    row = b.rows('SELECT status,reviewer,order_id FROM purchase_request WHERE id=%s', (rid,))[0]
    assert row == dict(status='pending', reviewer=None, order_id=None)
    assert len(b.rows('SELECT id FROM purchase_order')) == 1
    result = expect(other.post(f'/api/operations/requests/{rid}/review', json=review))
    assert result['status'] == ('approved' if decision == 'approve' else 'rejected')
    assert b.rows('SELECT reviewer FROM purchase_request WHERE id=%s', (rid,))[0]['reviewer'] == b.users['other']['actor']
    if decision == 'approve':
        row = b.rows('SELECT created_by,status FROM purchase_order WHERE id=%s', (result['order_id'],))[0]
        assert row == dict(created_by=b.users['approver']['id'], status=2)
    else:
        assert result['order_id'] is None
        assert len(b.rows('SELECT id FROM purchase_order')) == 1


@pytest.mark.parametrize('decision', ['approve', 'reject'])
def test_order_self_review_denied_other_approver_succeeds(business, decision):
    b = business
    owner, other = b.clients['approver'], b.clients['other']
    oid = expect(owner.post('/api/orders/create', json=order_body()), 201)['data']['id']
    detail = expect(owner.get(f'/api/operations/receiving/{oid}'))
    review = dict(actor=b.users['other']['actor'], decision=decision,
                  reason='synthetic review', expected_version=detail['review_version'])
    expect(owner.post(f'/api/operations/receiving/{oid}/review', json=review), 403)
    assert b.rows('SELECT status FROM purchase_order WHERE id=%s', (oid,))[0]['status'] == 1
    assert not b.rows('SELECT id FROM purchase_order_review WHERE order_id=%s', (oid,))
    result = expect(other.post(f'/api/operations/receiving/{oid}/review', json=review))
    assert result['status'] == (2 if decision == 'approve' else 5)
    row = b.rows('SELECT actor,decision FROM purchase_order_review WHERE order_id=%s', (oid,))[0]
    assert row == dict(actor=b.users['other']['actor'], decision=decision)
    assert b.rows('SELECT created_by FROM purchase_order WHERE id=%s', (oid,))[0]['created_by'] == b.users['approver']['id']


def test_approver_can_use_all_business_domains(business):
    b = business
    client, other = b.clients['approver'], b.clients['other']
    for path in ('/api/dashboard/summary', '/api/dashboard/suppliers', '/api/dashboard/orders',
                 '/api/dashboard/inventory', '/api/operations/receiving', '/api/operations/requests',
                 '/api/operations/movements', '/api/operations/master/part',
                 '/api/operations/master/supplier', '/api/operations/master-changes',
                 '/api/operations/suggestions'):
        expect(client.get(path))
    oid = expect(client.post('/api/orders/create', json=order_body()), 201)['data']['id']
    expect(client.put(f'/api/orders/update/{oid}', json={'remark': 'approver procurement'}))
    expect(client.post('/api/operations/stock', json=stock_body()))
    expect(client.post('/api/inventory/adjust', json=dict(
        partId=1, delta=1, reason='synthetic', requestId=uuid4().hex)))
    expect(client.post('/api/operations/receipts', json=dict(
        request_id=uuid4().hex, delivery_number='AUTH-DN', actor='forged', reason='synthetic',
        order_id=1, lines=[dict(detail_id=1, accepted=1, rejected=0)])))
    assert b.rows('SELECT current_quantity FROM inventory')[0]['current_quantity'] == 21
    assert b.rows('SELECT actor FROM goods_receipt')[0]['actor'] == b.users['approver']['actor']
    proposal = dict(request_id=uuid4().hex, actor='forged', reason='synthetic',
                    entity='supplier', entity_id=1,
                    values={'supplier_code': 'AUTH-S1', 'name': 'Reviewed name', 'status': 1})
    mid = expect(other.post('/api/operations/master-changes', json=proposal))['id']
    assert b.rows('SELECT status FROM master_change WHERE id=%s',(mid,))[0]['status']=='applied'
    assert b.rows('SELECT name FROM supplier WHERE id=1')[0]['name'] == 'Reviewed name'
    mid = expect(client.post('/api/operations/master-changes', json={**proposal, 'request_id': uuid4().hex}))['id']
    assert b.rows('SELECT actor FROM master_change WHERE id=%s', (mid,))[0]['actor'] == b.users['approver']['actor']
    rid = expect(b.clients['purchaser'].post('/api/operations/requests', json=request_body()))['id']
    expect(client.post(f'/api/operations/requests/{rid}/review', json=dict(
        actor='forged', decision='approve', reason='synthetic')))
    # Business approvers do not acquire account administration privileges.
    expect(client.get('/api/auth/users'), 403)


def test_approver_master_edit_applies_immediately_with_audit_and_retry(business):
    b=business
    payload=dict(request_id=uuid4().hex,actor='forged',reason='explicit supplier edit',entity='supplier',entity_id=1,values={'supplier_code':'AUTH-S1','name':'Directly edited supplier','status':2})
    result=expect(b.clients['approver'].post('/api/operations/master-changes',json=payload))
    assert result['status']=='applied'
    assert expect(b.clients['approver'].post('/api/operations/master-changes',json=payload))==result
    assert b.rows('SELECT name,status FROM supplier WHERE id=1')[0]=={'name':'Directly edited supplier','status':2}
    row=b.rows('SELECT * FROM master_change WHERE id=%s',(result['id'],))[0]
    assert row['actor']==b.users['approver']['actor'] and row['reviewer'] is None
    assert row['previous'] and row['proposed'] and row['created_at']
    assert len(b.rows('SELECT * FROM master_change'))==1
    expect(b.clients['purchaser'].post('/api/operations/master-changes',json={**payload,'request_id':uuid4().hex}),403)
