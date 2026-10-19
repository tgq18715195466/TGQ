"""Shared authentication for both ASGI apps; no DB connection at import time.

Mount ``router`` and ``app.add_middleware(AuthMiddleware)`` on each app.
Bearer tokens support trusted MCP forwarding; browser tabs select HttpOnly cookies.
Auth errors deliberately fail closed. Context variables cover the entire response.
"""
import hashlib
import hmac
import json
import os
import re
import secrets
from contextvars import ContextVar
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlsplit

from fastapi import APIRouter, HTTPException
from starlette.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

current_identity: ContextVar[dict | None] = ContextVar('current_identity', default=None)
current_token: ContextVar[str | None] = ContextVar('current_token', default=None)
COOKIE_NAME = 'erp_session'
current_cookie_name: ContextVar[str] = ContextVar('current_cookie_name', default=COOKIE_NAME)
SESSION_SECONDS = 8 * 60 * 60
MAX_BODY = 1024 * 1024
PERMISSIONS = {
    'purchaser': ['procurement', 'read_procurement', 'read_inventory', 'chat'],
    'warehouse': ['warehouse', 'read_procurement', 'read_inventory', 'chat'],
    'approver': ['procurement', 'warehouse', 'approve', 'master', 'read_procurement', 'read_inventory', 'chat'],
    'admin': ['procurement', 'warehouse', 'approve', 'master', 'read_procurement', 'read_inventory', 'chat', 'users'],
}
router = APIRouter(prefix='/api/auth', tags=['账号认证'])


def can(user, permission):
    return bool(user and user.get('enabled', True) and permission in PERMISSIONS.get(user.get('role'), ()))


def identity(row):
    if not row or not row.get('enabled', True) or row.get('role') not in PERMISSIONS:
        return None
    return {'id': int(row['id']), 'username': row['username'],
            'display_name': row['display_name'], 'role': row['role'],
            'actor': f"账户#{row['id']} {row['username']}",
            'permissions': list(PERMISSIONS[row['role']])}


def hash_password(password):
    if not isinstance(password, str) or not 12 <= len(password) <= 256:
        raise HTTPException(422, '密码长度必须为12至256个字符')
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 600_000)
    return f'pbkdf2_sha256$600000${salt.hex()}${digest.hex()}'


def verify_password(password, encoded):
    try:
        algorithm, rounds, salt, expected = encoded.split('$')
        if algorithm != 'pbkdf2_sha256' or int(rounds) != 600_000 or len(password) > 256:
            return False
        if len(salt) != 32 or len(expected) != 64:
            return False
        actual = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), int(rounds))
        return hmac.compare_digest(actual, bytes.fromhex(expected))
    except (TypeError, ValueError, AttributeError):
        return False


def token_hash(token):
    return hashlib.sha256(token.encode('ascii')).hexdigest()


def authenticate_token(token):
    """Synchronous, fresh DB lookup. Invalid -> None; DB outage -> HTTP 503."""
    if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{40,128}', token):
        return None
    return identity(repository.authenticate(token_hash(token)))


# Exact methods and anchored patterns: new API routes require an explicit policy.
_RULES = [
    ('GET', r'/api/dashboard/summary', ()),
    ('GET', r'/api/dashboard/(?:suppliers|orders(?:/\d+)?)', ('read_procurement',)),
    ('GET', r'/api/dashboard/inventory', ('read_inventory',)),
    ('GET', r'/api/suppliers/search', ('read_procurement',)),
    ('GET', r'/api/parts/(?:page|search|supplier/\d+)', ('read_inventory',)),
    ('GET', r'/api/inventory/warning', ('read_inventory',)),
    ('POST', r'/api/inventory/adjust', ('warehouse',)),
    ('POST', r'/api/orders/create', ('procurement',)),
    ('PUT', r'/api/orders/update/\d+', ('procurement',)),
    ('GET', r'/api/orders/search-details', ('read_procurement',)),
    ('GET', r'/api/operations/receiving(?:/\d+)?', ('warehouse','procurement','approve')),
    ('POST', r'/api/operations/receiving/\d+/review', ('approve',)),
    ('POST', r'/api/operations/receiving/\d+/start', ('procurement',)),
    ('POST', r'/api/operations/(?:receipts|stock)', ('warehouse',)),
    ('GET', r'/api/operations/movements', ('warehouse',)),
    ('GET', r'/api/operations/receipts/daily', ('read_inventory',)),
    ('GET', r'/api/operations/master/(?:part|supplier)', ('master',)),
    ('GET', r'/api/operations/master-changes', ('master',)),
    ('POST', r'/api/operations/master-changes', ('master',)),
    ('POST', r'/api/operations/master-changes/\d+/review', ('approve',)),
    ('GET', r'/api/operations/suggestions', ('read_inventory',)),
    ('GET', r'/api/operations/requests', ('read_procurement',)),
    ('GET', r'/api/operations/verification', ('procurement',)),
    ('POST', r'/api/operations/requests', ('procurement',)),
    ('POST', r'/api/operations/requests/\d+/review', ('approve',)),
    ('GET', r'/api/chat/[^/]+(?:/history)?', ('chat',)),
    ('POST', r'/api/chat/(?:stream|[^/]+/resume|[^/]+/tools/[^/]+/retry)', ('chat',)),
    ('GET', r'/api/history(?:/[^/]+/messages)?', ('chat',)),
    ('DELETE', r'/api/history/[^/]+', ('chat',)),
    ('PATCH', r'/api/history/[^/]+', ('chat',)),
    ('GET', r'/api/artifacts/[^/]+', ('chat',)),
    ('GET', r'/api/auth/me', ()),
    ('POST', r'/api/auth/(?:logout|password)', ()),
    ('GET', r'/api/auth/users', ('users',)),
    ('POST', r'/api/auth/users', ('users',)),
    ('PATCH', r'/api/auth/users/\d+', ('users',)),
]
PUBLIC = {('GET','/health'), ('GET','/health/ready'), ('GET','/api/auth/status'),
          ('POST','/api/auth/setup'), ('POST','/api/auth/login')}


def origin_allowed(origin, scope, headers):
    if not origin or origin == 'null':
        return False
    try:
        parsed = urlsplit(origin)
        if parsed.scheme not in ('http', 'https') or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
            return False
        # Explicit local development origins, never arbitrary ports/subdomains.
        configured = {entry.strip() for entry in os.getenv('AUTH_ALLOWED_ORIGINS', '').split(',') if entry.strip()}
        local = {f'http://{host}:{port}' for host in ('localhost','127.0.0.1') for port in (3000,3001)}
        if origin in local | configured:
            return True
        return origin == f"{scope.get('scheme', 'http')}://{headers.get('host', '')}"
    except ValueError:
        return False


def browser_cookie_name(scope, headers):
    """Select one tab's cookie without falling back to another tab's identity.

    The selector is not a credential. Images/downloads cannot set headers, so
    only artifact GETs may carry it in their URL. The secret stays HttpOnly.
    """
    selector = headers.get('x-erp-session')
    if selector is None and scope['method'] == 'GET' and re.fullmatch(r'/api/artifacts/[^/]+', scope['path']):
        values = parse_qs(scope.get('query_string', b'').decode('latin1'), keep_blank_values=True).get('erp_session')
        if values is not None:
            selector = values[0] if len(values) == 1 else ''
    if selector is None:
        return COOKIE_NAME  # Existing API clients retain their cookie contract.
    if not re.fullmatch(r'[0-9a-f]{32}', selector):
        raise HTTPException(400, '页面会话标识无效，请刷新页面后重试')
    return f'{COOKIE_NAME}_{selector}'


class AuthMiddleware:
    def __init__(self, app, authenticator=None, max_body=MAX_BODY, audit_recorder=None):
        self.app = app
        self.authenticator = authenticator or authenticate_token
        self.max_body = max_body
        self.audit_recorder = audit_recorder or record_audit

    async def __call__(self, scope, receive, send):
        if scope['type'] not in ('http', 'websocket'):
            return await self.app(scope, receive, send)
        if scope['type'] == 'websocket':
            return await send({'type': 'websocket.close', 'code': 1008})
        headers = {k.decode('latin1').lower(): v.decode('latin1') for k,v in scope.get('headers', [])}
        path, method = scope['path'], scope['method']
        clean_scope = dict(scope)
        clean_scope['headers'] = [(k,v) for k,v in scope.get('headers', []) if k.lower() not in (b'x-user-id', b'x-username')]
        ident = token = None
        try:
            cookie_name = browser_cookie_name(scope, headers)
            # API cookie mutations require Origin. Bearer-only service requests may omit it.
            bearer = headers.get('authorization', '')
            is_bearer = bearer.startswith('Bearer ')
            if (method, path) not in PUBLIC and path.startswith('/api'):
                if is_bearer:
                    token = bearer[7:]
                else:
                    cookie = SimpleCookie()
                    try:
                        cookie.load(headers.get('cookie', ''))
                        token = cookie[cookie_name].value if cookie_name in cookie else None
                    except Exception:
                        token = None
                if not token:
                    raise HTTPException(401, '请先登录')
                try:
                    ident = await run_in_threadpool(self.authenticator, token)
                except HTTPException:
                    raise
                except Exception as exc:
                    raise HTTPException(503, '认证服务暂不可用，请稍后重试') from exc
                if not ident or ident.get('role') not in PERMISSIONS or not ident.get('enabled', True):
                    raise HTTPException(401, '登录已失效，请重新登录')
                matched = next((perms for verb,pattern,perms in _RULES if method == verb and re.fullmatch(pattern,path)), None)
                if matched is None or (matched and not any(can(ident,p) for p in matched)):
                    raise HTTPException(403, '没有此操作权限')
                clean_scope['headers'] += [(b'x-user-id', f"user-{ident['id']}".encode()), (b'x-username', ident['username'].encode('utf-8'))]
            if method not in ('GET','HEAD','OPTIONS') and path.startswith('/api/'):
                if 'origin' in headers or not is_bearer or path in ('/api/auth/setup','/api/auth/login'):
                    if not origin_allowed(headers.get('origin'), scope, headers):
                        raise HTTPException(403, '请求来源不可信，请从本系统页面操作')
            # Read once, bound memory for auth and business writes; never read GET bodies.
            if path.startswith('/api/') and method in ('POST','PUT','PATCH','DELETE'):
                body = bytearray()
                while True:
                    message = await receive()
                    if message['type'] == 'http.disconnect':
                        return
                    body.extend(message.get('body', b''))
                    if len(body) > self.max_body:
                        raise HTTPException(413, '请求内容过大')
                    if not message.get('more_body', False):
                        break
                inject = ident and (path.startswith('/api/operations/') or path == '/api/orders/create' or re.fullmatch(r'/api/orders/update/\d+',path))
                if inject:
                    try:
                        payload = json.loads(body)
                        if not isinstance(payload, dict):
                            raise ValueError()
                    except (ValueError, UnicodeDecodeError):
                        raise HTTPException(422, '请求内容必须为JSON对象')
                    if path.startswith('/api/operations/'):
                        payload['actor'] = ident['actor']
                    if path.startswith('/api/orders/'):
                        payload.pop('created_by', None)
                        payload.pop('createdBy', None)
                        if path == '/api/orders/create':
                            payload['createdBy'] = ident['id']
                    body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
                clean_scope['headers'] = [(k,v) for k,v in clean_scope['headers'] if k.lower() != b'content-length']
                clean_scope['headers'].append((b'content-length', str(len(body)).encode()))
                original_receive, delivered = receive, False
                async def replay():
                    nonlocal delivered
                    if not delivered:
                        delivered = True
                        return {'type':'http.request', 'body':bytes(body), 'more_body':False}
                    return await original_receive()
                receive = replay
        except HTTPException as exc:
            return await JSONResponse({'detail':exc.detail},status_code=exc.status_code)(scope,receive,send)
        identity_mark = current_identity.set(ident)
        token_mark = current_token.set(token)
        cookie_mark = current_cookie_name.set(cookie_name)
        try:
            audit_failed = False
            async def audited_send(message):
                nonlocal audit_failed
                if audit_failed:
                    return
                business_write = ident and method in ('POST','PUT','PATCH','DELETE') and path.startswith(('/api/orders/','/api/operations/','/api/inventory/'))
                if message['type'] == 'http.response.start' and business_write and 200 <= message['status'] < 300:
                    try:
                        await run_in_threadpool(self.audit_recorder, user_id=ident['id'], actor=ident['actor'], method=method, path=path, status=message['status'])
                    except Exception:
                        # Business commit precedes middleware: never imply rollback or encourage a blind retry.
                        audit_failed = True
                        return await JSONResponse({'detail':'业务可能已保存，但审计写入失败；请先核对结果，勿重复提交'}, status_code=503)(scope,receive,send)
                await send(message)
            await self.app(clean_scope, receive, audited_send)
        finally:
            current_cookie_name.reset(cookie_mark)
            current_token.reset(token_mark)
            current_identity.reset(identity_mark)

# Repository methods own transactions. The connector is injectable for offline tests.
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Literal
import pymysql
from fastapi import Response
from pydantic import BaseModel, ConfigDict, Field


def _connect():
    from backend.app import _connect as connect
    return connect()


class MySQLRepository:
    def __init__(self, connector=None):
        self.connector = connector or _connect

    @contextmanager
    def transaction(self):
        conn = None
        try:
            conn = self.connector()
            conn.begin()
            with conn.cursor() as cursor:
                yield cursor
            conn.commit()
        except HTTPException:
            if conn is not None:
                conn.rollback()
            raise
        except pymysql.IntegrityError as exc:
            if conn is not None:
                conn.rollback()
            raise HTTPException(409, '账号已存在或数据冲突') from exc
        except Exception as exc:
            if conn is not None:
                try:
                    conn.rollback()
                except Exception:
                    pass
            raise HTTPException(503, '认证服务暂不可用，请稍后重试') from exc
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception as exc:
                    raise HTTPException(503, '认证服务暂不可用，请稍后重试') from exc

    @staticmethod
    def lock_bootstrap(cur):
        cur.execute('INSERT INTO auth_bootstrap(id,initialized) VALUES(1,FALSE) ON DUPLICATE KEY UPDATE id=VALUES(id)')
        cur.execute('SELECT initialized FROM auth_bootstrap WHERE id=1 FOR UPDATE')
        row = cur.fetchone()
        if row is None:
            raise HTTPException(503, '认证表尚未初始化，请运行数据库迁移')
        return row

    @staticmethod
    def require_admin(cur, admin_id):
        cur.execute('SELECT * FROM auth_user WHERE id=%s FOR UPDATE', (admin_id,))
        if not can(cur.fetchone(), 'users'):
            raise HTTPException(403, '只有管理员可以管理账号')

    @staticmethod
    def add_session(cur, user_id):
        token = secrets.token_urlsafe(32)
        expires = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=SESSION_SECONDS)
        cur.execute('INSERT INTO auth_session(token_hash,user_id,expires_at) VALUES(%s,%s,%s)', (token_hash(token),user_id,expires))
        return token

    def record_audit(self, *, user_id, actor, method, path, status):
        with self.transaction() as cur:
            cur.execute('INSERT INTO auth_audit(user_id,actor,method,path,status) VALUES(%s,%s,%s,%s,%s)', (user_id,actor,method,path,status))

    def setup_required(self):
        with self.transaction() as cur:
            cur.execute('SELECT initialized FROM auth_bootstrap WHERE id=1')
            row = cur.fetchone()
            if row and row['initialized']:
                return False
            cur.execute('SELECT id FROM auth_user LIMIT 1')
            return cur.fetchone() is None

    def setup(self, username, display_name, password):
        hashed = hash_password(password)
        with self.transaction() as cur:
            state = self.lock_bootstrap(cur)
            cur.execute('SELECT id FROM auth_user LIMIT 1')
            if state['initialized'] or cur.fetchone():
                raise HTTPException(409, '系统已初始化，请登录')
            cur.execute('INSERT INTO auth_user(username,display_name,password_hash,role,enabled) VALUES(%s,%s,%s,%s,%s)', (username,display_name,hashed,'admin',True))
            user_id = cur.lastrowid
            cur.execute('UPDATE auth_bootstrap SET initialized=1 WHERE id=1')
            token = self.add_session(cur,user_id)
            return identity(dict(id=user_id,username=username,display_name=display_name,role='admin')),token

    def login(self, username, password):
        with self.transaction() as cur:
            cur.execute('SELECT * FROM auth_user WHERE username=%s FOR UPDATE', (username,))
            row = cur.fetchone()
            # Perform the same KDF for unknown usernames; never return password hashes.
            encoded = row['password_hash'] if row else 'pbkdf2_sha256$600000$' + '00'*16 + '$' + '00'*32
            valid = verify_password(password,encoded)
            user = identity(row)
            if not valid or not user:
                raise HTTPException(401, '用户名或密码错误，或账号已停用')
            return user,self.add_session(cur,user['id'])

    def authenticate(self, hashed_token):
        with self.transaction() as cur:
            cur.execute('SELECT u.* FROM auth_session s JOIN auth_user u ON u.id=s.user_id WHERE s.token_hash=%s AND s.expires_at>UTC_TIMESTAMP()', (hashed_token,))
            return cur.fetchone()

    def logout(self, token):
        with self.transaction() as cur:
            cur.execute('DELETE FROM auth_session WHERE token_hash=%s', (token_hash(token),))

    def users(self, admin_id):
        with self.transaction() as cur:
            self.require_admin(cur,admin_id)
            cur.execute('SELECT id,username,display_name,role,enabled FROM auth_user ORDER BY id')
            return [{**row,'enabled':bool(row['enabled'])} for row in cur.fetchall()]

    def create_user(self, admin_id, username, display_name, password, role):
        hashed = hash_password(password)
        with self.transaction() as cur:
            self.lock_bootstrap(cur)
            self.require_admin(cur,admin_id)
            cur.execute('SELECT id FROM auth_user WHERE username=%s', (username,))
            if cur.fetchone():
                raise HTTPException(409, '用户名已存在')
            cur.execute('INSERT INTO auth_user(username,display_name,password_hash,role,enabled) VALUES(%s,%s,%s,%s,%s)', (username,display_name,hashed,role,True))
            return identity(dict(id=cur.lastrowid,username=username,display_name=display_name,role=role))

    def update_user(self, admin_id, user_id, changes):
        if 'password' in changes:
            changes = dict(changes)
            changes['password_hash'] = hash_password(changes.pop('password'))
        with self.transaction() as cur:
            self.lock_bootstrap(cur)
            self.require_admin(cur,admin_id)
            cur.execute('SELECT * FROM auth_user WHERE id=%s FOR UPDATE', (user_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, '账号不存在')
            if row['role']=='admin' and row['enabled'] and (changes.get('role',row['role'])!='admin' or not changes.get('enabled',row['enabled'])):
                cur.execute("SELECT id FROM auth_user WHERE role='admin' AND enabled=1 FOR UPDATE")
                if len(cur.fetchall()) <= 1:
                    raise HTTPException(409, '不能停用或降级最后一个管理员')
            allowed = ('role','enabled','password_hash','display_name')
            for key in allowed:
                if key in changes:
                    cur.execute(f'UPDATE auth_user SET {key}=%s WHERE id=%s', (changes[key],user_id))
            if 'password_hash' in changes or changes.get('enabled') is False:
                cur.execute('DELETE FROM auth_session WHERE user_id=%s', (user_id,))
            row.update(changes)
            return {key: bool(row[key]) if key=='enabled' else row[key] for key in ('id','username','display_name','role','enabled')}

    def change_password(self, user_id, old_password, new_password):
        hashed = hash_password(new_password)
        with self.transaction() as cur:
            cur.execute('SELECT * FROM auth_user WHERE id=%s FOR UPDATE', (user_id,))
            row = cur.fetchone()
            if not identity(row) or not verify_password(old_password,row['password_hash']):
                raise HTTPException(401, '原密码错误或登录已失效')
            cur.execute('UPDATE auth_user SET password_hash=%s WHERE id=%s', (hashed,user_id))
            cur.execute('DELETE FROM auth_session WHERE user_id=%s', (user_id,))


def record_audit(**row):
    """No request/response payloads, query strings, cookies or tokens are persisted."""
    return repository.record_audit(**row)


repository = MySQLRepository()
Role = Literal['purchaser','warehouse','approver','admin']


class AuthInput(BaseModel):
    model_config = ConfigDict(extra='forbid')


class SetupInput(AuthInput):
    username: str = Field(min_length=1,max_length=64,pattern=r'^[A-Za-z0-9_.-]+$')
    display_name: str = Field(default='',max_length=100)
    password: str = Field(min_length=12,max_length=256)


class LoginInput(AuthInput):
    username: str = Field(min_length=1,max_length=64)
    password: str = Field(min_length=1,max_length=256)


class CreateUserInput(SetupInput):
    role: Role


class UpdateUserInput(AuthInput):
    role: Role | None = None
    enabled: bool | None = Field(default=None,strict=True)
    password: str | None = Field(default=None,min_length=12,max_length=256)
    display_name: str | None = Field(default=None,min_length=1,max_length=100)


class PasswordInput(AuthInput):
    old_password: str = Field(min_length=1,max_length=256)
    new_password: str = Field(min_length=12,max_length=256)


def _user(admin=False):
    user = current_identity.get()
    if not user:
        raise HTTPException(401, '请先登录')
    if admin and not can(user,'users'):
        raise HTTPException(403, '只有管理员可以管理账号')
    return user


def cookie_secure():
    return os.getenv('AUTH_COOKIE_SECURE','false').lower() in ('true','1','yes')


def session_response(response, user, token):
    response.set_cookie(current_cookie_name.get(),token,max_age=SESSION_SECONDS,httponly=True,secure=cookie_secure(),samesite='strict',path='/')
    response.headers['Cache-Control'] = 'no-store'
    return {'user':user}


@router.get('/status')
def status():
    return {'setup_required':repository.setup_required()}


@router.post('/setup')
def setup(payload: SetupInput, response: Response):
    user, token = repository.setup(payload.username,payload.display_name or payload.username,payload.password)
    return session_response(response,user,token)


@router.post('/login')
def login(payload: LoginInput, response: Response):
    user, token = repository.login(payload.username,payload.password)
    return session_response(response,user,token)


@router.post('/logout')
def logout(response: Response):
    _user()
    repository.logout(current_token.get())
    response.delete_cookie(current_cookie_name.get(),path='/',httponly=True,secure=cookie_secure(),samesite='strict')
    return {'user':None}


@router.get('/me')
def me(response: Response):
    response.headers['Cache-Control'] = 'no-store'
    return {'user':_user()}


@router.get('/users')
def users():
    return {'users':repository.users(_user(True)['id'])}


@router.post('/users')
def create_user(payload: CreateUserInput):
    return {'user':repository.create_user(_user(True)['id'],payload.username,payload.display_name or payload.username,payload.password,payload.role)}


@router.patch('/users/{user_id}')
def update_user(user_id: int, payload: UpdateUserInput):
    changes = payload.model_dump(exclude_unset=True)
    if not changes or any(value is None for value in changes.values()):
        raise HTTPException(422, '更新字段不能为空')
    return {'user':repository.update_user(_user(True)['id'],user_id,changes)}


@router.post('/password')
def password(payload: PasswordInput, response: Response):
    repository.change_password(_user()['id'],payload.old_password,payload.new_password)
    response.delete_cookie(current_cookie_name.get(),path='/',httponly=True,secure=cookie_secure(),samesite='strict')
    return {'user':None}
