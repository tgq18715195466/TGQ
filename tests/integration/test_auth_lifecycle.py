"""Offline HTTP lifecycle tests against a transactional SQLite SQL adapter.

Only the adapter translates MySQL syntax. Production repository and endpoints
are exercised unchanged; no real MySQL credentials or business data are used.
"""
import sqlite3
import threading
from contextlib import contextmanager
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend import auth as a

class Cursor:
    def __init__(self, connection):
        self.cursor = connection.cursor()
    def __enter__(self): return self
    def __exit__(self, *args): self.cursor.close()
    def execute(self, sql, params=()):
        sql = sql.replace('%s', '?').replace(' FOR UPDATE','').replace('UTC_TIMESTAMP()', "datetime('now')")
        sql = sql.replace('ON DUPLICATE KEY UPDATE id=VALUES(id)', 'ON CONFLICT(id) DO NOTHING')
        params = tuple(value.isoformat(' ') if hasattr(value, 'isoformat') else value for value in params)
        return self.cursor.execute(sql, params)
    def fetchone(self):
        row=self.cursor.fetchone()
        return dict(row) if row else None
    def fetchall(self): return [dict(row) for row in self.cursor.fetchall()]
    @property
    def lastrowid(self): return self.cursor.lastrowid

class Connection:
    def __init__(self,path):
        self.db=sqlite3.connect(path, timeout=10)
        self.db.row_factory=sqlite3.Row
    def begin(self): self.db.execute('BEGIN IMMEDIATE')
    def cursor(self): return Cursor(self.db)
    def commit(self): self.db.commit()
    def rollback(self): self.db.rollback()
    def close(self): self.db.close()

@pytest.fixture
def system(tmp_path,monkeypatch):
    path=tmp_path/'auth.sqlite'
    conn=sqlite3.connect(path)
    conn.executescript('''CREATE TABLE auth_bootstrap(id INTEGER PRIMARY KEY, initialized INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE auth_user(id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, display_name TEXT, password_hash TEXT, role TEXT, enabled INTEGER DEFAULT 1);
    CREATE TABLE auth_audit(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,actor TEXT,method TEXT,path TEXT,status INTEGER);
    CREATE TABLE auth_session(token_hash TEXT PRIMARY KEY,user_id INTEGER,expires_at DATETIME);
    ''')
    conn.close()
    assert hasattr(a,'MySQLRepository'), 'MySQL repository尚未实现'
    repo=a.MySQLRepository(lambda:Connection(path))
    monkeypatch.setattr(a,'repository',repo)
    app=FastAPI()
    app.include_router(a.router)
    app.add_middleware(a.AuthMiddleware)
    return TestClient(app,headers={'Origin':'http://testserver'}),repo,path


def setup(c):
    r=c.post('/api/auth/setup',json={'username':'admin','display_name':'管理员','password':'strong password 123'})
    assert r.status_code==200, r.text
    return r


def test_two_tabs_share_cookie_jar_but_keep_distinct_accounts(system):
    c, repo, _ = system
    setup(c)
    for name, role in [('reviewer', 'approver'), ('buyer', 'purchaser')]:
        assert c.post('/api/auth/users', json={'username': name, 'password': 'strong password 123',
                      'role': role, 'display_name': name}).status_code == 200
    reviewer = {'X-ERP-Session': 'a' * 32}
    buyer = {'X-ERP-Session': 'b' * 32}
    for name, headers in [('reviewer', reviewer), ('buyer', buyer)]:
        response = c.post('/api/auth/login', headers=headers,
                          json={'username': name, 'password': 'strong password 123'})
        assert response.status_code == 200
        assert 'httponly' in response.headers['set-cookie'].lower()
        assert set(response.json()) == {'user'}  # Never expose the credential to JavaScript.
    for _ in range(2):  # Refresh both tabs, then repeat.
        assert c.get('/api/auth/me', headers=reviewer).json()['user']['role'] == 'approver'
        assert c.get('/api/auth/me', headers=buyer).json()['user']['role'] == 'purchaser'
    assert c.post('/api/auth/logout', headers=buyer).status_code == 200
    assert c.get('/api/auth/me', headers=buyer).status_code == 401
    assert c.get('/api/auth/me', headers=reviewer).json()['user']['role'] == 'approver'
    assert c.post('/api/auth/password', headers=reviewer,
                  json={'old_password': 'strong password 123', 'new_password': 'new strong password 123'}).status_code == 200
    assert c.get('/api/auth/me', headers=reviewer).status_code == 401


def test_unknown_tab_never_falls_back_to_shared_login(system):
    c, _, _ = system
    setup(c)
    assert c.get('/api/auth/me', headers={'X-ERP-Session': 'c' * 32}).status_code == 401
    assert c.get('/api/auth/me', headers={'X-ERP-Session': '../invalid'}).status_code == 400
    assert c.post('/api/auth/login', headers={'X-ERP-Session': '../invalid'},
                  json={'username': 'admin', 'password': 'strong password 123'}).status_code == 400


def test_lifecycle_and_immediate_revocation(system):
    c,repo,path=system
    assert c.get('/api/auth/status').json()=={'setup_required':True}
    r=setup(c)
    assert r.json()['user']['actor']=='账户#1 admin'
    assert 'httponly' in r.headers['set-cookie'].lower()
    assert 'samesite=strict' in r.headers['set-cookie'].lower()
    assert 'Max-Age=28800' in r.headers['set-cookie']
    token=c.cookies.get(a.COOKIE_NAME)
    assert a.authenticate_token(token)['role']=='admin'
    with sqlite3.connect(path) as db:
        stored=db.execute('SELECT token_hash FROM auth_session').fetchone()[0]
        assert stored!=token and stored==a.token_hash(token)
    assert c.post('/api/auth/setup',json={'username':'other','password':'strong password 123','display_name':'other'}).status_code==409
    assert c.patch('/api/auth/users/1',json={'enabled':False}).status_code==409
    assert c.patch('/api/auth/users/1',json={'role':'warehouse'}).status_code==409
    assert c.patch('/api/auth/users/1',json={'username':'renamed'}).status_code==422
    r=c.post('/api/auth/users',json={'username':'worker','password':'strong password 123','role':'purchaser','display_name':'采购'})
    assert r.status_code==200,r.text
    uid=r.json()['user']['id']
    worker=TestClient(c.app,headers={'Origin':'http://testserver'})
    assert worker.post('/api/auth/login',json={'username':'worker','password':'wrong password 123'}).status_code==401
    assert worker.post('/api/auth/login',json={'username':'worker','password':'strong password 123'}).status_code==200
    worker_token=worker.cookies.get(a.COOKIE_NAME)
    assert worker.get('/api/auth/users').status_code==403
    assert c.patch(f'/api/auth/users/{uid}',json={'role':'warehouse'}).status_code==200
    assert a.authenticate_token(worker_token)['role']=='warehouse'
    assert c.patch(f'/api/auth/users/{uid}',json={'enabled':False}).status_code==200
    assert a.authenticate_token(worker_token) is None
    assert c.post('/api/auth/logout').status_code==200
    assert a.authenticate_token(token) is None


def test_password_revokes_sessions_and_expiry(system):
    c,repo,path=system
    setup(c)
    token=c.cookies.get(a.COOKIE_NAME)
    assert c.post('/api/auth/password',json={'old_password':'wrong','new_password':'new strong password 123'}).status_code==401
    assert c.post('/api/auth/password',json={'old_password':'strong password 123','new_password':'new strong password 123'}).status_code==200
    assert a.authenticate_token(token) is None
    assert c.post('/api/auth/login',json={'username':'admin','password':'new strong password 123'}).status_code==200
    token=c.cookies.get(a.COOKIE_NAME)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE auth_session SET expires_at='2000-01-01'")
    assert a.authenticate_token(token) is None


def test_concurrent_setup_only_one(system):
    from concurrent.futures import ThreadPoolExecutor
    c,repo,path=system
    barrier=threading.Barrier(2)
    def attempt(i):
        client=TestClient(c.app,headers={'Origin':'http://testserver'})
        barrier.wait()
        return client.post('/api/auth/setup',json={'username':f'admin{i}','display_name':'Admin','password':'strong password 123'}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt,range(2)))==[200,409]


def test_database_errors_fail_closed(monkeypatch):
    app=FastAPI()
    def broken(token): raise RuntimeError('database is down')
    app.add_middleware(a.AuthMiddleware,authenticator=broken)
    assert TestClient(app).get('/api/dashboard/summary',headers={'Authorization':'Bearer token'}).status_code==503


def test_parallel_admin_demotion_keeps_one(system):
    from concurrent.futures import ThreadPoolExecutor
    c,repo,path=system
    setup(c)
    second=c.post('/api/auth/users',json={'username':'admin2','password':'strong password 123','role':'admin','display_name':'Second'}).json()['user']
    other=TestClient(c.app,headers={'Origin':'http://testserver'})
    assert other.post('/api/auth/login',json={'username':'admin2','password':'strong password 123'}).status_code==200
    barrier=threading.Barrier(2)
    def demote(pair):
        client,uid=pair
        barrier.wait()
        return client.patch(f'/api/auth/users/{uid}',json={'role':'purchaser'}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes=list(pool.map(demote,[(c,1),(other,second['id'])]))
    assert sorted(outcomes)==[200,409]
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM auth_user WHERE role='admin' AND enabled=1").fetchone()[0]==1


def test_audit_persisted_without_secrets(system):
    c,repo,path=system
    setup(c)
    @c.app.put('/api/orders/update/{order_id}')
    def update(order_id:int):return {'code':200}
    r=c.put('/api/orders/update/9',json={'remark':'private text','createdBy':99})
    assert r.status_code==200
    with sqlite3.connect(path) as db:
        row=db.execute('SELECT user_id,actor,method,path,status FROM auth_audit').fetchone()
    assert row==(1,'账户#1 admin','PUT','/api/orders/update/9',200)


def test_admin_password_reset_revokes_and_reenable_does_not_resurrect(system):
    c,repo,path=system
    setup(c)
    uid=c.post('/api/auth/users',json={'username':'worker','password':'strong password 123','role':'warehouse'}).json()['user']['id']
    worker=TestClient(c.app,headers={'Origin':'http://testserver'})
    worker.post('/api/auth/login',json={'username':'worker','password':'strong password 123'})
    token=worker.cookies.get(a.COOKIE_NAME)
    assert c.patch(f'/api/auth/users/{uid}',json={'password':'updated password 123'}).status_code==200
    assert a.authenticate_token(token) is None
    worker.post('/api/auth/login',json={'username':'worker','password':'updated password 123'})
    token=worker.cookies.get(a.COOKIE_NAME)
    c.patch(f'/api/auth/users/{uid}',json={'enabled':False})
    c.patch(f'/api/auth/users/{uid}',json={'enabled':True})
    assert a.authenticate_token(token) is None


def test_secure_cookie_and_auth_validation(system,monkeypatch):
    c,repo,path=system
    monkeypatch.setenv('AUTH_COOKIE_SECURE','true')
    r=setup(c)
    assert '; Secure' in r.headers['set-cookie']
    token=c.cookies.get(a.COOKIE_NAME)
    headers={'Authorization':f'Bearer {token}'}
    assert c.post('/api/auth/users',headers=headers,json={'username':'x','password':'strong password 123','role':'superuser'}).status_code==422
    assert c.post('/api/auth/users',headers=headers,json={'username':'x','password':'short','role':'admin'}).status_code==422
    assert c.patch('/api/auth/users/1',headers=headers,json={'enabled':None}).status_code==422
    assert c.patch('/api/auth/users/1',headers=headers,json={}).status_code==422


def test_repository_connection_outage_is_503(monkeypatch):
    def broken(): raise OSError('offline')
    monkeypatch.setattr(a,'repository',a.MySQLRepository(broken))
    app=FastAPI()
    app.include_router(a.router)
    app.add_middleware(a.AuthMiddleware)
    c=TestClient(app)
    assert c.get('/api/auth/status').status_code==503
    assert c.get('/api/auth/me',headers={'Authorization':'Bearer '+'a'*43}).status_code==503


def test_repository_close_failure_is_503(monkeypatch):
    class Cursor:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def execute(self,*args):pass
        def fetchone(self):return {'initialized':True}
    class Connection:
        def begin(self):pass
        def cursor(self):return Cursor()
        def commit(self):pass
        def close(self):raise OSError('connection lost on close')
    monkeypatch.setattr(a,'repository',a.MySQLRepository(Connection))
    app=FastAPI()
    app.include_router(a.router)
    app.add_middleware(a.AuthMiddleware)
    assert TestClient(app,raise_server_exceptions=False).get('/api/auth/status').status_code==503
