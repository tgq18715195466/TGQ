"""Browser purchase API contract. Run with project Python/pytest, without services."""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend import app as backend
from backend import auth
from api_view.web_main import app as web_app
from tests.integration.test_auth_lifecycle import Connection


@pytest.fixture
def purchaser(tmp_path, monkeypatch):
    path = tmp_path / 'purchase_routes.sqlite'
    token = 'isolated-purchaser-session-' * 2
    with sqlite3.connect(path) as db:
        db.executescript('''
        CREATE TABLE auth_user(id INTEGER PRIMARY KEY, username TEXT, display_name TEXT,
                               role TEXT, enabled INTEGER);
        CREATE TABLE auth_session(token_hash TEXT PRIMARY KEY, user_id INTEGER, expires_at TEXT);
        CREATE TABLE part(id INTEGER PRIMARY KEY, part_code TEXT, name TEXT, model TEXT,
                          specification TEXT, unit TEXT, purchase_price NUMERIC,
                          suggested_retail_price NUMERIC, stock_warning_value INTEGER,
                          supplier_id INTEGER, category TEXT, deleted INTEGER DEFAULT 0);
        INSERT INTO auth_user VALUES(1,'isolated','Isolated purchaser','purchaser',1);
        INSERT INTO part(id,part_code,name,purchase_price,supplier_id) VALUES
            (11,'P11','Material A',0,2),(12,'P12','Material B',8,2),(13,'P13','Other supplier',9,3);
        ''')
        db.execute('INSERT INTO auth_session VALUES(?,1,?)', (auth.token_hash(token),'2099-01-01'))
    monkeypatch.setattr(backend, '_connect', lambda: Connection(path))
    monkeypatch.setattr(auth, 'repository', auth.MySQLRepository(lambda: Connection(path)))
    return token


@pytest.mark.parametrize('app', [backend.app, web_app], ids=['backend-8080', 'browser-web-8090'])
def test_purchaser_can_page_supplier_materials_with_browser_cookie(app, purchaser):
    # No context manager: do not start Agent lifecycle or connect to external services.
    client = TestClient(app)
    client.cookies.set(auth.COOKIE_NAME, purchaser)
    records = []
    for current in (1,2):
        response = client.get('/api/parts/page', params={'supplierId':2,'current':current,'size':1})
        assert response.status_code == 200, response.text
        data = response.json()['data']
        assert data['total'] == 2
        records.extend(data['records'])
    assert [(r['id'],r['supplier_id'],r['name'],r['purchase_price']) for r in records] == [
        (11,2,'Material A',0),(12,2,'Material B',8)]


@pytest.mark.parametrize('app', [backend.app, web_app], ids=['backend-8080', 'browser-web-8090'])
def test_purchase_materials_still_require_login(app):
    assert TestClient(app).get('/api/parts/page?supplierId=2&size=100').status_code == 401


def test_browser_purchase_submit_route_exists():
    # Inspect registration only; never submit an order, even in the isolated database.
    assert 'post' in web_app.openapi()['paths'].get('/api/orders/create', {})
