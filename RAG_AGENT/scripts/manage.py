"""Local administrative operations; never accepts raw SQL or arbitrary model settings."""
import argparse
import getpass
import hashlib
import json
from pathlib import Path
import time
from uuid import UUID

import jwt
from sqlalchemy import select

from app.config import settings
from app.db import transaction
from app.models import EvalCase, EvalDataset, Tenant, User, uid
from app.password_auth import AUTH_TENANT_ID, set_login_password
from app.repository import audit


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    seed = sub.add_parser('seed'); seed.add_argument('--name', default='本地演示租户')
    token = sub.add_parser('token'); token.add_argument('--tenant', type=UUID, required=True)
    token.add_argument('--user', type=UUID, required=True)
    password = sub.add_parser('set-password'); password.add_argument('--tenant', type=UUID, required=True)
    password.add_argument('--user', type=UUID, required=True)
    password.add_argument('--username', required=True)
    dataset = sub.add_parser('import-eval'); dataset.add_argument('--tenant', type=UUID, required=True)
    dataset.add_argument('--user', type=UUID, required=True); dataset.add_argument('--file', required=True)
    dataset.add_argument('--version', required=True)
    args = parser.parse_args()
    if args.command == 'seed':
        tenant_id, user_id = uid(), uid()
        with transaction(tenant_id) as db:
            db.add(Tenant(id=tenant_id, name=args.name)); db.flush()
            db.add(User(id=user_id, tenant_id=tenant_id, name='本地管理员'))
        result = {'tenant_id': tenant_id, 'user_id': user_id}
        Path('.runtime').mkdir(exist_ok=True)
        Path('.runtime/demo.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        print(json.dumps(result))
    elif args.command == 'token':
        config = settings()
        if len(config.jwt_secret) < 32:
            raise SystemExit('Configure JWT_SECRET with at least 32 bytes')
        with transaction(str(args.tenant)) as db:
            user = db.scalar(select(User).where(User.tenant_id == str(args.tenant), User.id == str(args.user), User.status == 'ACTIVE'))
            if not user: raise SystemExit('User not found')
        print(jwt.encode({'tenant_id': str(args.tenant), 'sub': str(args.user), 'iss': config.jwt_issuer,
                          'aud': config.jwt_audience, 'iat': int(time.time()), 'exp': int(time.time())+3600},
                         config.jwt_secret, algorithm='HS256'))
    elif args.command == 'set-password':
        tenant, user_id = str(args.tenant), str(args.user)
        with transaction(tenant) as db:
            user = db.scalar(select(User).where(User.tenant_id == tenant, User.id == user_id,
                                                User.status == 'ACTIVE'))
            if not user:
                raise SystemExit('Active user not found')
        secret = getpass.getpass('New password (12 or more characters): ')
        confirmation = getpass.getpass('Repeat password: ')
        if secret != confirmation:
            raise SystemExit('Passwords do not match')
        with transaction(AUTH_TENANT_ID) as db:
            set_login_password(db, args.username, secret, tenant, user_id)
        print('Password updated. Existing sessions expire after at most one hour.')
    else:
        data = Path(args.file).read_bytes()
        cases = [json.loads(line) for line in data.decode('utf-8').splitlines() if line.strip()]
        digest = hashlib.sha256(data).hexdigest()
        tenant = str(args.tenant)
        with transaction(tenant) as db:
            dataset = db.scalar(select(EvalDataset).where(EvalDataset.tenant_id == tenant,
                               EvalDataset.name == Path(args.file).stem, EvalDataset.version == args.version))
            if dataset:
                if dataset.sha256 != digest: raise SystemExit('Dataset version is immutable; choose a new version')
            else:
                dataset = EvalDataset(tenant_id=tenant, name=Path(args.file).stem, version=args.version, sha256=digest)
                db.add(dataset); db.flush()
                for case in cases:
                    if not {'id', 'question', 'expected_answer', 'evidence_chunk_ids', 'document_version', 'question_type', 'annotator'} <= case.keys():
                        raise ValueError('Invalid evaluation case')
                    db.add(EvalCase(tenant_id=tenant, dataset_id=dataset.id, case_key=case['id'], data_jsonb=case))
                audit(db, tenant, str(args.user), 'eval.dataset_imported', dataset.id)
            print(json.dumps({'dataset_id': dataset.id, 'count': len(cases)}))


if __name__ == '__main__':
    main()
