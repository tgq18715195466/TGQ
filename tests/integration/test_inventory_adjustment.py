import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pymysql
import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend import app as backend


# Transaction/validation tests deliberately exercise routers without authentication.
# Production middleware stays installed; real permissions have their own MySQL tests.
_transaction_app = FastAPI()
_transaction_app.include_router(backend.router)
_transaction_app.include_router(backend.dashboard_router)
_transaction_app.include_router(backend.operations_router)


class Database:
    """Execute real transactions in isolated memory; record MySQL locking SQL."""
    def __init__(self):
        self.db = sqlite3.connect(':memory:', check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            CREATE TABLE inventory(id INTEGER PRIMARY KEY, part_id INTEGER, current_quantity INTEGER, deleted INTEGER DEFAULT 0);
            INSERT INTO inventory VALUES(10, 1, 12, 0), (20, 2, 8, 0), (30, 3, 5, 1);
            CREATE TABLE stock_movement(id INTEGER PRIMARY KEY, part_id INTEGER, delta INTEGER, before_quantity INTEGER, after_quantity INTEGER, kind TEXT, reason TEXT, actor TEXT, document TEXT);
            CREATE TABLE inventory_adjustment(request_id TEXT PRIMARY KEY, part_id INTEGER, delta INTEGER, reason TEXT, before_quantity INTEGER, after_quantity INTEGER);
        ''')
        self.statements = []
        self.fail_ledger_update = False
        self.closed = 0

    def cursor(self):
        return Cursor(self)

    def begin(self):
        self.db.execute('BEGIN')

    def commit(self):
        self.db.commit()

    def rollback(self):
        self.db.rollback()

    def close(self):
        self.closed += 1

    def quantity(self, part=1):
        return self.db.execute('SELECT current_quantity FROM inventory WHERE part_id=?', (part,)).fetchone()[0]

    def ledger_count(self):
        return self.db.execute('SELECT COUNT(*) FROM inventory_adjustment').fetchone()[0]


class Cursor:
    def __init__(self, owner):
        self.owner = owner
        self.cursor = owner.db.cursor()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.cursor.close()

    def execute(self, sql, params=()):
        self.owner.statements.append(sql)
        if self.owner.fail_ledger_update and sql.startswith('UPDATE inventory_adjustment'):
            raise RuntimeError('synthetic ledger failure')
        try:
            self.cursor.execute(sql.replace('%s', '?').replace(' FOR UPDATE', ''), params)
        except sqlite3.IntegrityError as exc:
            raise pymysql.IntegrityError(1062, 'duplicate request') from exc
        self.rowcount = self.cursor.rowcount

    def fetchone(self):
        row = self.cursor.fetchone()
        return dict(row) if row is not None else None


@pytest.fixture
def setup(monkeypatch):
    db = Database()
    monkeypatch.setattr(backend, '_connect', lambda: db)
    with TestClient(_transaction_app) as client:
        yield db, client
    db.db.close()


def payload(**changes):
    return dict(partId=1, delta=-3, reason='synthetic correction', requestId='test-request') | changes


@pytest.mark.parametrize('changes', [
    {'delta': 0}, {'delta': 1.5}, {'delta': True}, {'delta': '2'},
    {'reason': ''}, {'reason': '  '}, {'requestId': ''}, {'requestId': '  '},
    {'partId': 0}, {'partId': True}, {'requestId': ' x '},
])
def test_invalid_adjustment_never_connects(setup, changes):
    db, client = setup
    assert client.post('/api/inventory/adjust', json=payload(**changes)).status_code == 422
    assert not db.statements


def test_request_id_mandatory(setup):
    db, client = setup
    body = payload()
    del body['requestId']
    assert client.post('/api/inventory/adjust', json=body).status_code == 422
    assert not db.statements


def test_adjustment_and_identical_retry(setup):
    db, client = setup
    first = client.post('/api/inventory/adjust', json=payload())
    assert first.status_code == 200
    assert first.json() == {'code': 200, 'data': payload() | {'beforeQuantity': 12, 'afterQuantity': 9}}
    # A later independent adjustment must not change the original retry result.
    assert client.post('/api/inventory/adjust', json=payload(delta=2, requestId='second')).status_code == 200
    assert client.post('/api/inventory/adjust', json=payload()).json() == first.json()
    assert db.quantity() == 11
    assert db.ledger_count() == 2
    assert db.db.execute('SELECT COUNT(*) FROM stock_movement').fetchone()[0] == 2
    assert any('FROM inventory ' in sql and 'FOR UPDATE' in sql for sql in db.statements)


@pytest.mark.parametrize('changes', [{'delta': 2}, {'partId': 2}, {'reason': 'changed'}, {'reason': 'synthetic correction '}])
def test_key_conflict_does_not_mutate(setup, changes):
    db, client = setup
    assert client.post('/api/inventory/adjust', json=payload()).status_code == 200
    assert client.post('/api/inventory/adjust', json=payload(**changes)).status_code == 409
    assert (db.quantity(), db.quantity(2), db.ledger_count()) == (9, 8, 1)


@pytest.mark.parametrize('changes,status', [({'delta': -13}, 409), ({'partId': 999}, 404), ({'partId': 3}, 404)])
def test_rejected_adjustment_rolls_back_key(setup, changes, status):
    db, client = setup
    assert client.post('/api/inventory/adjust', json=payload(**changes)).status_code == status
    assert db.quantity() == 12
    assert db.ledger_count() == 0
    assert client.post('/api/inventory/adjust', json=payload()).status_code == 200


def test_ledger_failure_rolls_back_inventory(setup):
    db, client = setup
    db.fail_ledger_update = True
    assert client.post('/api/inventory/adjust', json=payload()).status_code == 500
    assert db.quantity() == 12
    assert db.ledger_count() == 0
    assert db.closed == 1


def test_zero_balance_allowed_and_keys_case_sensitive(setup):
    db, client = setup
    assert client.post('/api/inventory/adjust', json=payload(delta=-12)).status_code == 200
    assert client.post('/api/inventory/adjust', json=payload(delta=1, requestId='TEST-REQUEST')).status_code == 200
    assert db.quantity() == 1
    assert db.ledger_count() == 2


@pytest.mark.asyncio
async def test_mutation_is_exclusive_to_order_agent(monkeypatch):
    from agent.tools import mcp_client
    names = ['inventory_warning', 'inventory_adjust', 'inventory_delete', 'part_query', 'order_create']
    client = SimpleNamespace(get_tools=AsyncMock(return_value=[SimpleNamespace(name=n) for n in names]))
    monkeypatch.setattr(mcp_client, 'MultiServerMCPClient', lambda _, **kwargs: client)
    with pytest.raises(ValueError, match='inventory_delete'):
        await mcp_client.load_mcp_tools({'erp-api': {}})
    client.get_tools.return_value = [SimpleNamespace(name=n) for n in names if n != 'inventory_delete']
    _, analyst, order, _ = await mcp_client.load_mcp_tools({'erp-api': {}})
    assert {t.name for t in analyst} == {'inventory_warning', 'part_query'}
    assert {t.name for t in order} == {'inventory_adjust', 'order_create', 'part_query'}


def test_inventory_approval_and_prompts():
    root = Path(__file__).resolve().parents[2]
    order = yaml.safe_load((root / 'src/agent/subagents/configs/procurement_order.yaml').read_text(encoding='utf-8'))
    analyst = yaml.safe_load((root / 'src/agent/subagents/configs/procurement_analyst.yaml').read_text(encoding='utf-8'))
    assert 'inventory_adjust' in order['tools']
    assert order['interrupt_on']['inventory_adjust']['allowed_decisions'] == ['approve', 'reject']
    assert 'inventory_adjust' not in analyst['tools']
    assert 'inventory_warning' in analyst['tools']
    assert 'OpenSandbox' not in order['system_prompt']
    assert 'Java' not in order['system_prompt']
    assert '完全相同的 Request Body' not in order['system_prompt']
    assert 'request_id' in order['system_prompt']
    assert '库存调整' in (root / 'src/agent/memory/prompts.py').read_text(encoding='utf-8')


def test_schema_only_migration_is_repeatable():
    from backend import migrate
    statements = []
    class DDL:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, sql): statements.append(sql)
    conn = SimpleNamespace(cursor=lambda: DDL(), commit=lambda: None)
    migrate.migrate(conn)
    migrate.migrate(conn)
    from backend.operations_schema import STATEMENTS as operations_schema
    from backend.auth_schema import STATEMENTS as auth_schema
    expected = migrate.STATEMENTS + operations_schema + auth_schema
    assert statements == list(expected) * 2
    assert all(sql.strip().upper().startswith('CREATE TABLE IF NOT EXISTS')
               for sql in statements if sql not in auth_schema)
    # Auth bootstrap's idempotent singleton insert is part of schema initialization.
    assert all(sql.strip().upper().startswith(('CREATE TABLE IF NOT EXISTS',
                                               'INSERT INTO AUTH_BOOTSTRAP'))
               for sql in auth_schema)
    assert any('inventory_adjustment' in sql and 'PRIMARY KEY' in sql for sql in statements)
    assert any('purchase_order_inventory_receipt' in sql for sql in statements)


def test_receipt_migration_matches_existing_timestamp_column():
    from backend.migrate import STATEMENTS
    receipt = next(sql for sql in STATEMENTS if 'purchase_order_inventory_receipt' in sql)
    assert 'posted_at' in receipt


def test_migration_entrypoint_closes_connection(monkeypatch):
    from backend import migrate
    from unittest.mock import Mock
    conn = Mock()
    monkeypatch.setattr(backend, '_connect', lambda: conn)
    apply = Mock()
    monkeypatch.setattr(migrate, 'migrate', apply)
    migrate.main()
    apply.assert_called_once_with(conn)
    conn.close.assert_called_once()


def test_container_environment_wins_over_dotenv():
    import os
    import subprocess
    import sys
    script = '''
import dotenv
original = dotenv.load_dotenv
def use_test_dotenv(*args, **kwargs):
    return original(stream=__import__('io').StringIO('MYSQL_HOST=dotenv-host'), **kwargs)
dotenv.load_dotenv = use_test_dotenv
from backend.app import MYSQL_CONFIG
assert MYSQL_CONFIG['host'] == 'container-host'
'''
    env = dict(os.environ, MYSQL_HOST='container-host')
    result = subprocess.run([sys.executable, '-c', script], env=env, capture_output=True)
    assert result.returncode == 0


def test_overflow_rolls_back_inventory_and_key(setup):
    db, client = setup
    db.db.execute('UPDATE inventory SET current_quantity=2147483647 WHERE part_id=1')
    db.db.commit()
    assert client.post('/api/inventory/adjust', json=payload(delta=1)).status_code == 409
    assert db.quantity() == 2147483647
    assert db.ledger_count() == 0
