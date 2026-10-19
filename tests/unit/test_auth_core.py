import importlib
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient


def auth():
    try:
        return importlib.import_module('backend.auth')
    except ModuleNotFoundError:
        pytest.fail('认证核心尚未实现')


def test_password_and_contract():
    a = auth()
    hashed = a.hash_password('a strong password 123')
    assert hashed != a.hash_password('a strong password 123')
    assert a.verify_password('a strong password 123', hashed)
    assert not a.verify_password('wrong password', hashed)
    assert not a.verify_password('password', 'invalid')
    assert a.current_identity.get() is None
    assert a.current_token.get() is None
    assert a.can({'role': 'purchaser'}, 'procurement')
    assert not a.can({'role': 'warehouse'}, 'approve')
    assert not a.can({'role': 'invented', 'permissions': ['users']}, 'users')


@pytest.fixture
def client():
    a = auth()
    app = FastAPI()
    @app.api_route('/{path:path}', methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE'])
    async def echo(request: Request):
        return {'user': a.current_identity.get(), 'token': a.current_token.get(),
                'headers': dict(request.headers),
                'body': await request.json() if request.method in ('POST','PUT','PATCH') else None}
    app.add_middleware(a.AuthMiddleware, audit_recorder=lambda **row:None, authenticator=lambda token: a.identity({'id': 7, 'username': 'buyer', 'display_name': '买家', 'role': 'purchaser', 'enabled': True}) if token == 'valid' else None)
    return TestClient(app)


def test_middleware_identity_and_creator(client):
    r = client.post('/api/orders/create', headers={'Authorization': 'Bearer valid', 'x-user-id': 'admin', 'x-username': 'fake'}, json={'createdBy': 99, 'created_by': 98})
    assert r.status_code == 200
    assert r.json()['body']['createdBy'] == 7
    assert 'created_by' not in r.json()['body']
    assert r.json()['user']['actor'] == '账户#7 buyer'
    assert r.json()['headers']['x-user-id'] == 'user-7'
    assert r.json()['headers']['x-username'] == 'buyer'
    r = client.put('/api/orders/update/1', headers={'Authorization': 'Bearer valid'}, json={'createdBy': 9, 'created_by': 9})
    assert r.json()['body'] == {}


def test_tab_cookie_selection_covers_artifacts_without_accepting_url_login_for_api(client):
    selector = 'd' * 32
    cookies = {'Cookie': f'erp_session=wrong; erp_session_{selector}=valid'}
    response = client.get(f'/api/artifacts/chart.svg?erp_session={selector}', headers=cookies)
    assert response.status_code == 200
    assert response.json()['user']['id'] == 7
    assert client.get('/api/artifacts/chart.svg?erp_session=invalid', headers=cookies).status_code == 400
    assert client.get(f'/api/artifacts/chart.svg?erp_session={selector}&erp_session={selector}', headers=cookies).status_code == 400
    assert client.get(f'/api/auth/me?erp_session={selector}', headers=cookies).status_code == 401
    assert client.get('/api/auth/me', headers={**cookies, 'X-ERP-Session': selector}).status_code == 200
    assert auth().current_cookie_name.get() == auth().COOKIE_NAME


def test_scoped_cookie_keeps_origin_checks_and_bearer_precedence(client):
    headers = {'Cookie': f'erp_session_{"e" * 32}=valid', 'X-ERP-Session': 'e' * 32}
    assert client.post('/api/orders/create', headers=headers, json={}).status_code == 403
    assert client.post('/api/orders/create', headers={**headers, 'Origin': 'https://evil.example'}, json={}).status_code == 403
    assert client.post('/api/orders/create', headers={**headers, 'Origin': 'http://localhost:3000'}, json={}).status_code == 200
    assert client.get('/api/auth/me', headers={**headers, 'Authorization': 'Bearer wrong'}).status_code == 401


@pytest.mark.parametrize('path,method,status', [('/api/orders/create','post',401),('/api/operations/stock','post',403),('/api/new-backdoor','get',403),('/api/auth/users','get',403)])
def test_fail_closed(client,path,method,status):
    headers = {} if status == 401 else {'Authorization': 'Bearer valid'}
    assert getattr(client,method)(path,headers=headers).status_code == status


def test_csrf_body_limit_and_context(client):
    headers = {'Cookie': 'erp_session=valid', 'Origin': 'https://evil.example'}
    assert client.post('/api/orders/create',headers=headers,json={}).status_code == 403
    headers['Origin'] = 'http://localhost:3000'
    assert client.post('/api/orders/create',headers=headers,json={}).status_code == 200
    assert client.post('/api/orders/create',headers={'Authorization':'Bearer valid'},content=b'x'*(1024*1024+1)).status_code == 413
    a = auth()
    assert a.current_identity.get() is None
    assert a.current_token.get() is None

@pytest.mark.parametrize('role,path,method,allowed', [
    ('purchaser','/api/dashboard/summary','GET',True),
    ('warehouse','/api/dashboard/suppliers','GET',True),
    ('warehouse','/api/parts/search','GET',True),
    ('purchaser','/api/operations/receiving/1','GET',True),
    ('warehouse','/api/orders/search-details','GET',True),
    ('approver','/api/operations/master/supplier','GET',True),
    ('admin','/api/operations/master/part','GET',True),
    ('approver','/api/operations/requests/1/review','POST',True),
    ('purchaser','/api/operations/requests/1/review','POST',False),
    ('warehouse','/api/operations/receipts','POST',True),
    ('warehouse','/api/operations/requests','POST',False),
    ('admin','/api/unknown','GET',False),
    ('approver','/api/auth/users','GET',False),
])
def test_policy_matrix(role,path,method,allowed):
    a=auth()
    app=FastAPI()
    @app.api_route('/{path:path}',methods=['GET','POST'])
    def ok(): return {'ok':True}
    app.add_middleware(a.AuthMiddleware,audit_recorder=lambda **row:None,authenticator=lambda token:a.identity(dict(id=8,username='test',display_name='Test',role=role)))
    result=TestClient(app).request(method,path,headers={'Authorization':'Bearer valid'},json={})
    assert result.status_code==(200 if allowed else 403)


def test_migration_is_additive_and_contains_auth():
    from backend import migrate,auth_schema
    from backend.operations_schema import STATEMENTS as operations
    recorded=[]
    class Cursor:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def execute(self,sql):recorded.append(sql)
    class Connection:
        def cursor(self):return Cursor()
        def commit(self):pass
    migrate.migrate(Connection())
    assert len(recorded)==len(migrate.STATEMENTS)+len(operations)+len(auth_schema.STATEMENTS)
    assert all(sql.strip().upper().startswith('CREATE TABLE IF NOT EXISTS') for sql in recorded)
    assert all(sql in recorded for sql in auth_schema.STATEMENTS)
    assert not any('INSERT INTO auth_user' in sql for sql in recorded)


@pytest.mark.parametrize('path', ['/api/operations/stock','/api/operations/receiving/1/review'])
def test_actor_overwrite(path):
    a=auth()
    app=FastAPI()
    @app.post('/{path:path}')
    async def echo(request:Request):return await request.json()
    app.add_middleware(a.AuthMiddleware,audit_recorder=lambda **row:None,authenticator=lambda token:a.identity(dict(id=4,username='stable',display_name='Renamable',role='admin')))
    r=TestClient(app).post(path,headers={'Authorization':'Bearer valid'},json={'actor':'forged'})
    assert r.status_code==200
    assert r.json()['actor']=='账户#4 stable'


def test_stream_context_reset_and_get_does_not_read():
    import asyncio
    a=auth()
    async def scenario():
        events=[]
        async def downstream(scope,receive,send):
            assert a.current_identity.get()['id']==3
            assert a.current_token.get()=='valid'
            await send({'type':'http.response.start','status':200,'headers':[]})
            await asyncio.sleep(0)
            assert a.current_identity.get()['actor']=='账户#3 u'
            await send({'type':'http.response.body','body':b'chunk','more_body':True})
            await asyncio.sleep(0)
            assert a.current_token.get()=='valid'
            await send({'type':'http.response.body','body':b'end'})
        async def receive():raise AssertionError('GET must not consume the body')
        async def send(event):events.append(event)
        middleware=a.AuthMiddleware(downstream,authenticator=lambda token:a.identity(dict(id=3,username='u',display_name='U',role='admin')))
        await middleware({'type':'http','method':'GET','path':'/api/chat/thread','headers':[(b'authorization',b'Bearer valid')]},receive,send)
        assert len(events)==3
        assert a.current_identity.get() is None and a.current_token.get() is None
    asyncio.run(scenario())


def test_context_resets_after_stream_exception():
    import asyncio
    a=auth()
    async def scenario():
        async def downstream(scope,receive,send):
            assert a.current_identity.get() is not None
            raise RuntimeError('stream failed')
        async def io(*args):return {}
        middleware=a.AuthMiddleware(downstream,authenticator=lambda token:a.identity(dict(id=3,username='u',display_name='U',role='admin')))
        with pytest.raises(RuntimeError):
            await middleware({'type':'http','method':'GET','path':'/api/chat/thread','headers':[(b'authorization',b'Bearer valid')]},io,io)
        assert a.current_identity.get() is None and a.current_token.get() is None
    asyncio.run(scenario())


def test_successful_writes_audit_metadata_only():
    a=auth()
    rows=[]
    app=FastAPI()
    @app.put('/api/orders/update/{id}')
    def ok(id:int):return {'code':200}
    app.add_middleware(a.AuthMiddleware,authenticator=lambda token:a.identity(dict(id=3,username='u',display_name='U',role='admin')),audit_recorder=lambda **row:rows.append(row))
    r=TestClient(app).put('/api/orders/update/42',headers={'Authorization':'Bearer secret'},json={'remark':'sensitive body','password':'never log me'})
    assert r.status_code==200
    assert rows==[{'user_id':3,'actor':'账户#3 u','method':'PUT','path':'/api/orders/update/42','status':200}]


def test_audit_failure_not_reported_as_success():
    a=auth()
    def fail(**row):raise RuntimeError('audit unavailable')
    app=FastAPI()
    @app.put('/api/orders/update/{id}')
    def ok(id:int):return {'code':200}
    app.add_middleware(a.AuthMiddleware,authenticator=lambda token:a.identity(dict(id=3,username='u',display_name='U',role='admin')),audit_recorder=fail)
    r=TestClient(app).put('/api/orders/update/42',headers={'Authorization':'Bearer secret'},json={})
    assert r.status_code==503
    assert '核对' in r.json()['detail']

@pytest.mark.parametrize('origin', ['http://127.0.0.1:3000','http://127.0.0.1:3001','https://erp.example.test'])
def test_explicit_origin_allowlist(client,monkeypatch,origin):
    monkeypatch.setenv('AUTH_ALLOWED_ORIGINS','https://erp.example.test')
    r=client.post('/api/orders/create',headers={'Cookie':'erp_session=valid','Origin':origin},json={})
    assert r.status_code==200


def test_inventory_payload_is_compatible():
    a=auth()
    app=FastAPI()
    @app.post('/api/inventory/adjust')
    async def echo(request:Request):return await request.json()
    app.add_middleware(a.AuthMiddleware,authenticator=lambda token:a.identity(dict(id=3,username='u',display_name='U',role='admin')),audit_recorder=lambda **row:None)
    payload={'partId':1,'delta':2,'reason':'count','requestId':'abc'}
    assert TestClient(app).post('/api/inventory/adjust',headers={'Authorization':'Bearer valid'},json=payload).json()==payload

@pytest.mark.parametrize('origin', ['null','https://evil.test','http://localhost:3002','http://localhost:3000.evil.test','http://127.0.0.1:3000/path','http://localhost:3000/','https://user@localhost:3000'])
def test_reject_unsafe_origin(client,origin):
    assert client.post('/api/orders/create',headers={'Cookie':'erp_session=valid','Origin':origin},json={}).status_code==403


def test_cookie_write_requires_origin(client):
    assert client.post('/api/orders/create',headers={'Cookie':'erp_session=valid'},json={}).status_code==403
    assert client.post('/api/auth/login',json={}).status_code==403


def test_unknown_role_and_forged_headers_are_not_identity():
    a=auth()
    app=FastAPI()
    app.add_middleware(a.AuthMiddleware,authenticator=lambda token:{'id':1,'role':'superuser','permissions':['users']})
    c=TestClient(app)
    assert c.get('/api/auth/users',headers={'Authorization':'Bearer valid'}).status_code==401
    assert c.get('/api/auth/me',headers={'x-user-id':'user-1','x-username':'admin'}).status_code==401


def test_parallel_stream_contexts_are_isolated():
    import asyncio
    a=auth()
    async def scenario():
        arrived=0
        all_ready=asyncio.Event()
        async def downstream(scope,receive,send):
            nonlocal arrived
            user=a.current_identity.get()
            token=a.current_token.get()
            arrived+=1
            if arrived==2:all_ready.set()
            await all_ready.wait()
            await asyncio.sleep(0)
            assert a.current_identity.get()['id']==int(token)
            assert a.current_identity.get() is user
        async def io(*args):return {}
        middleware=a.AuthMiddleware(downstream,authenticator=lambda token:a.identity(dict(id=int(token),username=f'u{token}',display_name='U',role='admin')))
        async def request(token):
            await middleware({'type':'http','method':'GET','path':'/api/chat/thread','headers':[(b'authorization',f'Bearer {token}'.encode())]},io,io)
            assert a.current_identity.get() is None and a.current_token.get() is None
        await asyncio.gather(request('1'),request('2'))
    asyncio.run(scenario())

def test_begin_purchase_reuses_procurement_permission_and_real_identity(client):
    response=client.post('/api/operations/receiving/22/start',headers={'Authorization':'Bearer valid'},json={'actor':'fake'})
    assert response.status_code==200
    assert response.json()['body']['actor']=='账户#7 buyer'
    assert client.post('/api/operations/receiving/22/start',json={}).status_code==401

def test_warehouse_cannot_begin_purchase():
    a=auth();app=FastAPI()
    @app.post('/api/operations/receiving/{order_id}/start')
    def fake_route(order_id:int):return {'unexpected':True}
    app.add_middleware(a.AuthMiddleware,audit_recorder=lambda **row:None,authenticator=lambda token:a.identity({'id':8,'username':'warehouse','display_name':'仓管','role':'warehouse','enabled':True}))
    with TestClient(app) as client:
        assert client.post('/api/operations/receiving/22/start',headers={'Authorization':'Bearer valid'},json={}).status_code==403
