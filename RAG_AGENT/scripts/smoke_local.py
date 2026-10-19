"""Read-only smoke check of a running local API. Never prints its authentication token."""
import json
import re
from pathlib import Path

import httpx


def main():
    credential_lines = Path('.runtime/local_login.txt').read_text(encoding='utf-8').splitlines()
    credentials = dict(line.split(': ', 1) for line in credential_lines)
    with httpx.Client(base_url='http://127.0.0.1:8000', timeout=5) as client:
        ready = client.get('/health/ready'); assert ready.status_code == 200, ready.status_code
        schema = client.get('/openapi.json'); assert schema.status_code == 200
        home = client.get('/'); assert home.status_code == 200 and '掌柜智库' in home.text
        asset_path = re.search(r'src="(/assets/[^"]+\.js)"', home.text)
        assert asset_path, 'React bundle not referenced from homepage'
        javascript = client.get(asset_path.group(1)); assert javascript.status_code == 200
        assert '<div id="root"></div>' in home.text
        unauthorized = client.get('/api/v1/knowledge-bases')
        assert unauthorized.status_code == 401
        assert unauthorized.json()['error_code'] == 'AUTH_REQUIRED'
        login = client.post('/api/v1/auth/login', json={
            'username': credentials['用户名'], 'password': credentials['密码']})
        assert login.status_code == 200, login.status_code
        token = login.json()['access_token']
        authorized = client.get('/api/v1/knowledge-bases', headers={'Authorization': 'Bearer '+token})
        assert authorized.status_code == 200, authorized.status_code
        for route in ['me', 'system/status', 'chat/sessions', 'eval-datasets', 'eval-runs']:
            result = client.get('/api/v1/' + route, headers={'Authorization': 'Bearer '+token})
            assert result.status_code == 200, (route, result.status_code)
    print(json.dumps({'health_ready': ready.status_code, 'openapi': schema.status_code,
                      'unauthorized': unauthorized.status_code, 'authorized_kb_list': authorized.status_code,
                      'workspace': home.status_code, 'javascript': javascript.status_code,
                      'login': login.status_code, 'workspace_api_lists': 'passed'}))


if __name__ == '__main__':
    main()
