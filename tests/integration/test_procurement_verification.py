"""Read real isolated SQL records through the authenticated backend and MCP."""
import json
import sqlite3
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from backend import app as backend
from backend import auth


PATH = '/api/operations/verification'


class Cursor:
    def __init__(self, connection):
        self.connection = connection
        self.cursor = connection.db.cursor()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.cursor.close()

    def execute(self, sql, params=()):
        if self.connection.readonly:
            assert sql.lstrip().upper().startswith('SELECT '), sql
            assert 'FOR UPDATE' not in sql.upper(), sql
            self.connection.statements.append((sql, params))
        sql = sql.replace('%s', '?').replace('UTC_TIMESTAMP()', "datetime('now')")
        return self.cursor.execute(sql, params or ())

    def fetchone(self):
        row = self.cursor.fetchone()
        return self.record(row) if row else None

    def fetchall(self):
        return [self.record(row) for row in self.cursor.fetchall()]

    @staticmethod
    def record(row):
        # PyMySQL exposes DECIMAL columns as Decimal, unlike SQLite NUMERIC.
        return {key: Decimal(str(value)) if key in ('unit_price', 'total_amount') and value is not None
                else value for key, value in dict(row).items()}


class Connection:
    def __init__(self, path, readonly=False):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.readonly = readonly
        self.closed = False
        self.statements = []

    def begin(self):
        assert not self.readonly
        self.db.execute('BEGIN')

    def cursor(self):
        return Cursor(self)

    def commit(self):
        assert not self.readonly
        self.db.commit()

    def rollback(self):
        assert not self.readonly
        self.db.rollback()

    def close(self):
        self.closed = True
        self.db.close()


@pytest.fixture
def system(tmp_path, monkeypatch):
    path = tmp_path / 'verification.sqlite'
    with sqlite3.connect(path) as db:
        db.executescript('''
        CREATE TABLE supplier(id INTEGER PRIMARY KEY, name TEXT, deleted INTEGER DEFAULT 0,
            supplier_code TEXT, contact_person TEXT, phone TEXT, email TEXT, address TEXT,
            credit_rating TEXT DEFAULT 'B', status INTEGER DEFAULT 1);
        CREATE TABLE part(id INTEGER PRIMARY KEY, name TEXT, part_code TEXT, supplier_id INTEGER, deleted INTEGER DEFAULT 0);
        CREATE TABLE erp_operation(request_id TEXT PRIMARY KEY, result TEXT);
        CREATE TABLE purchase_request(id INTEGER PRIMARY KEY, part_id INTEGER, supplier_id INTEGER,
            quantity INTEGER, unit_price NUMERIC, expected_date TEXT, status TEXT, order_id INTEGER,
            reason TEXT, actor TEXT);
        CREATE TABLE purchase_order(id INTEGER PRIMARY KEY, order_number TEXT UNIQUE COLLATE NOCASE,
            status INTEGER, total_amount NUMERIC, expected_delivery_date TEXT, deleted INTEGER DEFAULT 0);
        CREATE TABLE order_detail(id INTEGER PRIMARY KEY, order_id INTEGER, part_id INTEGER,
            quantity INTEGER, unit_price NUMERIC, deleted INTEGER DEFAULT 0);
        CREATE TABLE auth_user(id INTEGER PRIMARY KEY, username TEXT, display_name TEXT, role TEXT, enabled INTEGER);
        CREATE TABLE auth_session(token_hash TEXT PRIMARY KEY, user_id INTEGER, expires_at TEXT);
        INSERT INTO supplier(id,name,deleted) VALUES(2, '真实供应商', 0), (3, '另一供应商', 0);
        INSERT INTO part VALUES(7, '真实机油泵', 'PC0007', 2, 0), (8, '真实滤芯', 'PC0008', 2, 0);
        INSERT INTO purchase_request VALUES(91, 7, 2, 3, 0.10, '2099-01-02', 'approved', 92, '真实需求', '采购员');
        INSERT INTO purchase_order VALUES(92, 'PO-REAL', 2, 0.30, '2099-01-02', 0), (93, 'PO-EMPTY', 1, 0, '2099-01-02', 0);
        INSERT INTO order_detail VALUES(101, 92, 7, 3, 0.10, 0), (102, 92, 8, 99, 100, 1);
        ''')
        db.execute('INSERT INTO erp_operation VALUES(?, ?)', ('req-real', json.dumps({'id': 91, 'status': 'pending', 'quantity': 999})))
        for uid, role in enumerate(('purchaser', 'warehouse', 'approver', 'admin'), 1):
            db.execute('INSERT INTO auth_user VALUES(?, ?, ?, ?, 1)', (uid, role, role, role))
            db.execute('INSERT INTO auth_session VALUES(?, ?, ?)', (auth.token_hash(role * 12), uid, '2099-01-01'))
    connections = []

    def connect():
        connection = Connection(path, readonly=True)
        connections.append(connection)
        return connection

    monkeypatch.setattr(backend, '_connect', connect)
    monkeypatch.setattr(auth, 'repository', auth.MySQLRepository(lambda: Connection(path)))
    with TestClient(backend.app) as client:
        yield SimpleNamespace(client=client, path=path, connections=connections)
    assert all(c.closed for c in connections)


def headers(role='purchaser'):
    return {'Authorization': 'Bearer ' + role * 12}


def verify(system, operation='purchase_request_create', **params):
    return system.client.get(PATH, params={'operation': operation, **params}, headers=headers())


@pytest.mark.parametrize('role,status', [(None, 401), ('invalid', 401), ('warehouse', 403),
                                        ('purchaser', 200), ('approver', 200), ('admin', 200)])
def test_authentication_and_procurement_permission(system, role, status):
    response = system.client.get(PATH, params={'operation': 'order_create', 'order_number': 'PO-REAL'},
                                 headers=headers(role) if role else {})
    assert response.status_code == status, response.text
    if status != 200:
        assert system.connections == []


def test_cookie_auth_and_revocation(system):
    system.client.cookies.set(auth.COOKIE_NAME, 'purchaser' * 12)
    params = {'operation': 'purchase_request_create', 'request_id': 'req-real'}
    assert system.client.get(PATH, params=params).status_code == 200
    with sqlite3.connect(system.path) as db:
        db.execute("UPDATE auth_user SET enabled=0 WHERE role='purchaser'")
    assert system.client.get(PATH, params=params).status_code == 401


def test_request_lookup_returns_real_current_record_not_stored_ack(system):
    response = verify(system, request_id='req-real')
    assert response.status_code == 200, response.text
    record = response.json()['data']['record']
    assert response.json()['ok'] and response.json()['data']['found'] is True
    assert record['id'] == 91 and record['status'] == 'approved'
    assert record['quantity'] == 3 and isinstance(record['unit_price'], str)
    assert Decimal(record['unit_price']) == Decimal('0.10')
    assert record['part_id'] == 7 and record['name'] == '真实机油泵'
    assert record['supplier_id'] == 2 and record['supplier_name'] == '真实供应商'
    assert record['expected_date'] == '2099-01-02' and record['reason'] == '真实需求'
    with sqlite3.connect(system.path) as db:
        db.execute("UPDATE purchase_request SET quantity=4, status='rejected' WHERE id=91")
    record = verify(system, request_id='req-real').json()['data']['record']
    assert record['quantity'] == 4 and record['status'] == 'rejected'


@pytest.mark.parametrize('result', [None, '{}', '{"id":9999,"status":"pending"}',
                                  '{"id":true,"status":"pending"}', '[]'])
def test_ack_without_actual_request_is_not_found(system, result):
    with sqlite3.connect(system.path) as db:
        db.execute('INSERT INTO erp_operation VALUES(?, ?)', ('req-missing', result))
    response = verify(system, request_id='req-missing', order_number='PO-REAL')
    assert response.status_code == 200, response.text
    assert response.json() == {'ok': True, 'data': {'found': False, 'record': None}}


def test_missing_request_id_does_not_fall_back_to_other_entity(system):
    response = verify(system, request_id='absent', order_number='PO-REAL')
    assert response.json() == {'ok': True, 'data': {'found': False, 'record': None}}
    assert verify(system, request_id="req-real' OR 1=1 --").json()['data']['found'] is False


def test_order_snapshot_contains_actual_header_lines_names_and_supplier(system):
    response = verify(system, 'order_create', order_number='PO-REAL', request_id='absent')
    assert response.status_code == 200, response.text
    record = response.json()['data']['record']
    assert response.json()['data']['found'] is True
    assert record['id'] == 92 and record['order_number'] == 'PO-REAL' and record['status'] == 2
    assert record['supplier_id'] == 2 and record['expected_delivery_date'] == '2099-01-02'
    assert isinstance(record['total_amount'], str) and Decimal(record['total_amount']) == Decimal('0.30')
    assert len(record['lines']) == 1
    line = record['lines'][0]
    assert line['part_id'] == 7 and line['quantity'] == 3 and line['name'] == '真实机油泵'
    assert isinstance(line['unit_price'], str) and Decimal(line['unit_price']) == Decimal('0.10')
    assert record['order_detail'] == record['lines']


@pytest.mark.parametrize('number', ['absent', 'po-real', "PO-REAL' OR 1=1 --"])
def test_order_lookup_returns_only_exact_matched_entity(system, number):
    response = verify(system, 'order_create', order_number=number, request_id='req-real')
    assert response.json() == {'ok': True, 'data': {'found': False, 'record': None}}


def test_deleted_order_is_not_found(system):
    with sqlite3.connect(system.path) as db:
        db.execute('UPDATE purchase_order SET deleted=1 WHERE id=92')
    assert verify(system, 'order_create', order_number='PO-REAL').json()['data']['found'] is False


def test_incomplete_and_ambiguous_order_snapshots_remain_actual(system):
    record = verify(system, 'order_create', order_number='PO-EMPTY').json()['data']['record']
    assert record['lines'] == [] and record['supplier_id'] is None
    with sqlite3.connect(system.path) as db:
        db.execute('UPDATE part SET supplier_id=3 WHERE id=8')
        db.execute('UPDATE order_detail SET deleted=0 WHERE id=102')
    record = verify(system, 'order_create', order_number='PO-REAL').json()['data']['record']
    assert len(record['lines']) == 2 and record['supplier_id'] is None


def test_missing_master_data_does_not_hide_actual_order_lines(system):
    with sqlite3.connect(system.path) as db:
        db.execute('DELETE FROM part WHERE id=7')
    record = verify(system, 'order_create', order_number='PO-REAL').json()['data']['record']
    assert len(record['lines']) == 1 and record['lines'][0]['part_id'] == 7
    assert record['lines'][0]['name'] is None and record['supplier_id'] is None


@pytest.mark.parametrize('params', [{}, {'operation': 'stock'}, {'operation': 'order_create'},
                                  {'operation': 'purchase_request_create'},
                                  {'operation': 'order_create', 'order_number': ' '},
                                  {'operation': 'purchase_request_create', 'request_id': ' '},
                                  {'operation': 'purchase_request_create', 'request_id': 'x' * 129}])
def test_invalid_lookup_rejected_before_sql(system, params):
    response = system.client.get(PATH, params=params, headers=headers())
    assert response.status_code == 422, response.text
    assert system.connections == []


def test_database_failure_is_structured_and_retryable(system, monkeypatch):
    def unavailable():
        raise RuntimeError('private database address and credentials')
    monkeypatch.setattr(backend, '_connect', unavailable)
    response = verify(system, request_id='req-real')
    assert response.status_code == 503
    assert response.json()['detail'] == {'code': 'ERP_UNAVAILABLE', 'retryable': True}


class Registry:
    def __init__(self):
        self.tools = {}

    def tool(self, name):
        def register(function):
            self.tools[name] = function
            return function
        return register


def tool():
    from mcp_server.tools.verification_tools import register_verification_tools
    registry = Registry()
    register_verification_tools(registry)
    return registry.tools['procurement_verification']


def context(client):
    return SimpleNamespace(request_context=SimpleNamespace(lifespan_context={'http_client': client}))


async def test_mcp_forwards_request_local_auth_to_real_endpoint(system, monkeypatch):
    from mcp_server import http_base
    incoming = headers()
    monkeypatch.setattr(http_base, 'get_http_headers', lambda **kw: {k.lower(): v for k, v in incoming.items()})
    async with httpx.AsyncClient(base_url='http://testserver/api', transport=httpx.ASGITransport(app=backend.app),
                                 event_hooks={'request': [http_base.forward_authenticated_user]},
                                 headers={'Authorization': 'Bearer stale', 'x-user-id': 'forged'}) as client:
        call = tool()
        result = await call('purchase_request_create', request_id='req-real', ctx=context(client))
        assert result['ok'] and result['data']['record']['status'] == 'approved'
        incoming.update(headers('warehouse'))
        result = await call('order_create', order_number='PO-REAL', ctx=context(client))
        assert result['error'] == {'code': 'PERMISSION_DENIED', 'status': 403, 'retryable': False}
        incoming.clear()
        result = await call('order_create', order_number='PO-REAL', ctx=context(client))
        assert result['error'] == {'code': 'AUTH_REQUIRED', 'status': 401, 'retryable': False}


@pytest.mark.parametrize('status,code,retryable', [(401, 'AUTH_REQUIRED', False), (403, 'PERMISSION_DENIED', False),
                                                 (422, 'ERP_API_ERROR', False), (503, 'ERP_API_ERROR', True)])
async def test_mcp_http_failures_are_structured(status, code, retryable):
    async with httpx.AsyncClient(base_url='http://erp/api', transport=httpx.MockTransport(
            lambda req: httpx.Response(status, text='untrusted private detail'))) as client:
        result = await tool()('order_create', order_number='PO-REAL', ctx=context(client))
    assert result['ok'] is False
    assert result['error'] == {'code': code, 'status': status, 'retryable': retryable}


async def test_mcp_timeout_is_retryable_without_post():
    calls = []

    def unavailable(request):
        calls.append(request)
        raise httpx.ReadTimeout('private transport details', request=request)

    async with httpx.AsyncClient(base_url='http://erp/api', transport=httpx.MockTransport(unavailable)) as client:
        result = await tool()('purchase_request_create', request_id='req-real', ctx=context(client))
    assert result == {'ok': False, 'error': {'code': 'ERP_UNAVAILABLE', 'retryable': True}}
    assert len(calls) == 1 and calls[0].method == 'GET'
    assert dict(calls[0].url.params) == {'operation': 'purchase_request_create', 'request_id': 'req-real'}


@pytest.mark.parametrize('body', ['not json', '[]', '{"ok":true,"data":{"found":true}}',
                                '{"ok":true,"data":{"found":"false","record":{}}}'])
async def test_mcp_invalid_response_cannot_claim_verification(body):
    async with httpx.AsyncClient(base_url='http://erp/api', transport=httpx.MockTransport(
            lambda req: httpx.Response(200, text=body))) as client:
        result = await tool()('order_create', order_number='PO-REAL', ctx=context(client))
    # A malformed read is uncertain and can be queried again; retry never
    # replays the preceding business write.
    assert result == {'ok': False, 'error': {'code': 'INVALID_RESPONSE', 'retryable': True}}


async def test_server_registers_internal_tool_with_literal_operation_schema(monkeypatch):
    from fastmcp import Client
    from mcp_server.server_main import mcp
    async with Client(mcp) as client:
        tools = await client.list_tools()
        registered = next(t for t in tools if t.name == 'procurement_verification')
        assert registered.inputSchema['properties']['operation']['enum'] == ['order_create', 'purchase_request_create']
        assert registered.inputSchema['required'] == ['operation']


@pytest.mark.parametrize('action', ['purchase_request_create', 'order_create'])
async def test_fixed_graph_verifies_write_ack_against_real_mcp_snapshot(system, action):
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.types import Command
    from agent.procurement_workflow import create_procurement_graph
    from test_procurement_workflow import BusinessService

    service = BusinessService()
    actual_calls = []
    async with httpx.AsyncClient(base_url='http://testserver/api', transport=httpx.ASGITransport(app=backend.app),
                                 headers=headers()) as client:
        async def invoke(name, args):
            if name == 'procurement_verification':
                actual_calls.append(args)
                return await tool()(**args, ctx=context(client))
            return await service(name, args)

        draft = {'part_id': 7, 'quantity': 3, 'unit_price': '0.10', 'expected_date': '2099-01-02',
                 'reason': '用户确认需求', 'manual': True, 'request_id': 'untrusted'}
        if action == 'order_create':
            draft = {'supplier_id': 2, 'order_number': 'PO-REAL', 'expected_delivery_date': '2099-01-02',
                     'order_detail': [{'partId': 7, 'quantity': 3, 'unitPrice': '0.10'}]}
        mark = auth.current_identity.set(auth.identity({'id': 1, 'username': 'purchaser',
                                                       'display_name': '采购员', 'role': 'purchaser'}))
        try:
            graph = create_procurement_graph(invoke, checkpointer=InMemorySaver())
            config = {'configurable': {'thread_id': 'actual-verification-' + action}}
            state = await graph.ainvoke({'action': action, 'draft': draft}, config)
            assert state['stage'] == 'awaiting_confirmation'
            if action == 'purchase_request_create':
                # The graph generated the request key; provide its real persisted
                # locator independently from the minimal write acknowledgement.
                with sqlite3.connect(system.path) as db:
                    db.execute('INSERT INTO erp_operation VALUES(?, ?)',
                               (state['request_id'], '{"id":91,"status":"pending"}'))
            state = await graph.ainvoke(Command(resume={'decisions': [{'type': 'approve'}]}), config)
            assert state['verified'] is True and state['result']['verified'] is True
            assert state['result']['data']['status'] == ('approved' if action == 'purchase_request_create' else 2)
            assert len(actual_calls) == 1
            assert sum(name == action for name, _ in service.calls) == 1
        finally:
            auth.current_identity.reset(mark)


async def test_incomplete_actual_order_stays_in_recovery_and_retry_never_posts(system):
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.types import Command
    from agent.procurement_workflow import create_procurement_graph
    from test_procurement_workflow import BusinessService

    service = BusinessService()
    actual_calls = []
    async with httpx.AsyncClient(base_url='http://testserver/api', transport=httpx.ASGITransport(app=backend.app),
                                 headers=headers()) as client:
        async def invoke(name, args):
            if name == 'procurement_verification':
                actual_calls.append(args)
                return await tool()(**args, ctx=context(client))
            return await service(name, args)

        mark = auth.current_identity.set(auth.identity({'id': 1, 'username': 'purchaser',
                                                       'display_name': '采购员', 'role': 'purchaser'}))
        try:
            graph = create_procurement_graph(invoke, checkpointer=InMemorySaver())
            config = {'configurable': {'thread_id': 'actual-incomplete-order'}}
            draft = {'supplier_id': 2, 'order_number': 'PO-EMPTY', 'expected_delivery_date': '2099-01-02',
                     'order_detail': [{'partId': 7, 'quantity': 3, 'unitPrice': '0.10'}]}
            state = await graph.ainvoke({'action': 'order_create', 'draft': draft}, config)
            assert state['stage'] == 'awaiting_confirmation'
            state = await graph.ainvoke(Command(resume={'decisions': [{'type': 'approve'}]}), config)
            assert state['stage'] == 'awaiting_recovery' and state['verified'] is False
            assert state['__interrupt__'][0].value['write_attempted'] is True
            state = await graph.ainvoke(Command(resume={'workflow_action': 'retry'}), config)
            assert state['stage'] == 'awaiting_recovery' and state['verified'] is False
            with sqlite3.connect(system.path) as db:
                db.execute('INSERT INTO order_detail VALUES(103, 93, 7, 3, 0.10, 0)')
                db.execute('UPDATE purchase_order SET total_amount=0.30 WHERE id=93')
            state = await graph.ainvoke(Command(resume={'workflow_action': 'retry'}), config)
            assert state['verified'] is True and state['result']['data']['id'] == 93
            assert len(actual_calls) == 3
            assert sum(name == 'order_create' for name, _ in service.calls) == 1
        finally:
            auth.current_identity.reset(mark)


def test_supplier_paged_lookup_has_real_total_and_does_not_truncate_at_50(system):
    with sqlite3.connect(system.path) as db:
        db.executemany('INSERT INTO supplier(id,name,supplier_code,deleted) VALUES(?,?,?,?)',
                       [(i, f'分页供应商-{i}', f'PG{i:03}', int(i >= 133)) for i in range(10, 135)])
    params = {'name': '分页供应商-', 'paged': True, 'current': 1, 'size': 100}
    first = system.client.get('/api/suppliers/search', params=params, headers=headers())
    assert first.status_code == 200, first.text
    page = first.json()['data']
    assert isinstance(page, dict)
    assert page['total'] == 123 and page['current'] == 1 and page['size'] == 100
    assert [row['id'] for row in page['records']] == list(range(10, 110))
    second = system.client.get('/api/suppliers/search', params={**params, 'current': 2}, headers=headers()).json()['data']
    assert second['total'] == 123 and second['current'] == 2 and second['size'] == 100
    assert [row['id'] for row in second['records']] == list(range(110, 133))
    empty = system.client.get('/api/suppliers/search', params={**params, 'current': 3}, headers=headers()).json()['data']
    assert empty == {'records': [], 'total': 123, 'current': 3, 'size': 100}
    legacy = system.client.get('/api/suppliers/search', params={'name': '分页供应商-'}, headers=headers()).json()['data']
    assert isinstance(legacy, list) and len(legacy) == 50


@pytest.mark.parametrize('params,total,ids', [({'supplierId': 2}, 1, [2]),
                                           ({'name': '真实'}, 1, [2]),
                                           ({'supplierCode': 'PG010'}, 1, [10]),
                                           ({'name': '真实', 'supplierId': 3}, 0, []),
                                           ({'name': "' OR 1=1 --"}, 0, [])])
def test_supplier_pagination_count_uses_all_lookup_conditions(system, params, total, ids):
    with sqlite3.connect(system.path) as db:
        db.execute("INSERT INTO supplier(id,name,supplier_code) VALUES(10,'分页供应商','PG010')")
    response = system.client.get('/api/suppliers/search', params={'paged': True, **params}, headers=headers())
    assert response.status_code == 200, response.text
    page = response.json()['data']
    assert isinstance(page, dict)
    assert page['total'] == total
    assert [row['id'] for row in page['records']] == ids
    assert page['current'] == 1 and page['size'] == 100


@pytest.mark.parametrize('params', [{'size': 101}, {'size': 0}, {'current': 0}])
def test_supplier_pagination_validates_bounds_before_sql(system, params):
    response = system.client.get('/api/suppliers/search', params={'paged': True, **params}, headers=headers())
    assert response.status_code == 422
    assert system.connections == []
