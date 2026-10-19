"""Provision or reset the local demo user's password without printing it."""
import argparse
import json
from pathlib import Path
import secrets

from sqlalchemy import select

from app.config import settings
from app.db import transaction
from app.models import LoginCredential, User
from app.password_auth import AUTH_TENANT_ID, set_login_password


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--reset', action='store_true', help='replace the local admin password')
    args = parser.parse_args()
    if settings().environment != 'development':
        raise SystemExit('Local login setup is available only in development.')
    demo = json.loads(Path('.runtime/demo.json').read_text(encoding='utf-8'))
    tenant_id, user_id = demo['tenant_id'], demo['user_id']
    with transaction(tenant_id) as db:
        user = db.scalar(select(User).where(User.tenant_id == tenant_id, User.id == user_id,
                                            User.status == 'ACTIVE'))
        if user is None:
            raise SystemExit('The local demo user is not active.')
    username = 'admin'
    with transaction(AUTH_TENANT_ID) as db:
        existing = db.scalar(select(LoginCredential).where(LoginCredential.tenant_id == AUTH_TENANT_ID,
                                                            LoginCredential.username == username))
        if existing and not args.reset:
            print('Local admin login already exists. Use --reset to generate a new password.')
            return
        password = secrets.token_urlsafe(24)
        set_login_password(db, username, password, tenant_id, user_id)
    output = Path('.runtime/local_login.txt')
    output.parent.mkdir(exist_ok=True)
    output.write_text(f'用户名: {username}\n密码: {password}\n', encoding='utf-8')
    print(f'Local login ready. Read {output} on this computer for the initial password.')


if __name__ == '__main__':
    main()
