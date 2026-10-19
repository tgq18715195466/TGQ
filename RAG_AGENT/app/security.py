from dataclasses import dataclass
from uuid import UUID

import jwt
from sqlalchemy import select

from app.models import User


@dataclass(frozen=True)
class Identity:
    tenant_id: str
    user_id: str


def verify_token(authorization, config):
    if len(config.jwt_secret) < 32:
        raise RuntimeError('AUTH_NOT_CONFIGURED')
    if not authorization or not authorization.startswith('Bearer '):
        raise PermissionError('AUTH_REQUIRED')
    try:
        claims = jwt.decode(authorization[7:], config.jwt_secret, algorithms=['HS256'],
                            issuer=config.jwt_issuer, audience=config.jwt_audience,
                            options={'require': ['sub', 'tenant_id', 'exp', 'iss', 'aud']})
        return Identity(str(UUID(claims['tenant_id'])), str(UUID(claims['sub'])))
    except (jwt.PyJWTError, ValueError, KeyError) as exc:
        raise PermissionError('AUTH_REQUIRED') from exc


def require_active_user(db, identity):
    user = db.scalar(select(User).where(User.tenant_id == identity.tenant_id,
                      User.id == identity.user_id, User.status == 'ACTIVE'))
    if not user:
        raise PermissionError('AUTH_REQUIRED')
    return user
