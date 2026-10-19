"""Opt-in InnoDB tests; all writes are confined to a fresh random test database.

Run with ORDER_TEST_MYSQL=1 or INVENTORY_TEST_MYSQL=1. Never use the configured business database.
"""
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
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
def mysql_inventory(monkeypatch):
    if os.getenv('INVENTORY_TEST_MYSQL') != '1' and os.getenv('ORDER_TEST_MYSQL') != '1':
        pytest.skip('set ORDER_TEST_MYSQL=1 or INVENTORY_TEST_MYSQL=1 for isolated InnoDB tests')
    name = 'inventory_test_' + uuid4().hex
    config = {k: v for k, v in backend.MYSQL_CONFIG.items() if k != 'database'}
    admin = pymysql.connect(**config, connect_timeout=3, autocommit=True)
    with admin.cursor() as cur:
        cur.execute(f'CREATE DATABASE `{name}` CHARACTER SET utf8mb4')
    def connect():
        return pymysql.connect(**config, database=name, cursorclass=pymysql.cursors.DictCursor)
    try:
        conn = connect()
        try:
            with conn.cursor() as cur:
                cur.execute('CREATE TABLE inventory (id BIGINT PRIMARY KEY, part_id BIGINT UNIQUE, current_quantity INT NOT NULL, deleted TINYINT DEFAULT 0) ENGINE=InnoDB')
                cur.execute('CREATE TABLE purchase_order (id BIGINT PRIMARY KEY) ENGINE=InnoDB')
                cur.execute('INSERT INTO inventory(id, part_id, current_quantity) VALUES(1,1,12),(2,2,8)')
            conn.commit()
            migrate(conn)
            migrate(conn)
        finally:
            conn.close()
        monkeypatch.setattr(backend, '_connect', connect)
        yield connect
    finally:
        # The generated identifier never comes from configuration or user data.
        assert name.startswith('inventory_test_') and len(name) == 47
        with admin.cursor() as cur:
            cur.execute(f'DROP DATABASE `{name}`')
        admin.close()


def concurrent_requests(bodies):
    barrier = Barrier(len(bodies))
    def send(body):
        with TestClient(_transaction_app) as client:
            barrier.wait(timeout=10)
            return client.post('/api/inventory/adjust', json=body)
    with ThreadPoolExecutor(max_workers=len(bodies)) as pool:
        return list(pool.map(send, bodies))


def body(key='same', part=1, delta=-3):
    return {'partId': part, 'delta': delta, 'reason': 'synthetic test', 'requestId': key}


def state(connect):
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT part_id,current_quantity FROM inventory ORDER BY part_id')
            quantities = cur.fetchall()
            cur.execute('SELECT * FROM inventory_adjustment')
            ledger = cur.fetchall()
        return quantities, ledger
    finally:
        conn.close()


def test_mysql_concurrent_identical_requests_post_once(mysql_inventory):
    responses = concurrent_requests([body()] * 6)
    assert all(r.status_code == 200 for r in responses)
    assert all(r.json() == responses[0].json() for r in responses)
    quantities, ledger = state(mysql_inventory)
    assert quantities[0]['current_quantity'] == 9
    assert len(ledger) == 1
    assert (ledger[0]['before_quantity'], ledger[0]['after_quantity']) == (12, 9)


def test_mysql_conflicting_key_across_parts(mysql_inventory):
    responses = concurrent_requests([body(part=1), body(part=2)])
    assert sorted(r.status_code for r in responses) == [200, 409]
    quantities, ledger = state(mysql_inventory)
    assert sum(q['current_quantity'] for q in quantities) == 17
    assert len(ledger) == 1


def test_mysql_distinct_keys_serialize_no_negative_stock(mysql_inventory):
    responses = concurrent_requests([body(key='a', delta=-8), body(key='b', delta=-8)])
    assert sorted(r.status_code for r in responses) == [200, 409]
    quantities, ledger = state(mysql_inventory)
    assert quantities[0]['current_quantity'] == 4
    assert len(ledger) == 1


def test_mysql_migration_preserves_populated_tables(mysql_inventory):
    with TestClient(_transaction_app) as client:
        first = client.post('/api/inventory/adjust', json=body())
        assert first.status_code == 200
        conn = mysql_inventory()
        try:
            with conn.cursor() as cur:
                cur.execute('INSERT INTO purchase_order(id) VALUES(17)')
                cur.execute('INSERT INTO purchase_order_inventory_receipt(order_id) VALUES(17)')
            conn.commit()
            migrate(conn)
            migrate(conn)
            with conn.cursor() as cur:
                cur.execute('SELECT order_id FROM purchase_order_inventory_receipt')
                assert cur.fetchall() == [{'order_id': 17}]
        finally:
            conn.close()
        assert client.post('/api/inventory/adjust', json=body()).json() == first.json()
    quantities, ledger = state(mysql_inventory)
    assert quantities[0]['current_quantity'] == 9
    assert len(ledger) == 1
