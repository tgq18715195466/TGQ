from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings


@lru_cache
def engine():
    config = settings()
    return create_engine(config.postgres_dsn, pool_size=config.postgres_pool_size, pool_pre_ping=True)


@event.listens_for(Session, 'after_begin')
def set_tenant(session, transaction, connection):
    tenant = session.info.get('tenant_id')
    if connection.dialect.name == 'postgresql' and tenant:
        connection.execute(text("SELECT set_config('app.tenant_id', :tenant, true)"), {'tenant': tenant})


@contextmanager
def transaction(tenant_id, *, bind=None):
    with sessionmaker(bind=bind or engine(), expire_on_commit=False)(info={'tenant_id': str(tenant_id)}) as db:
        with db.begin():
            yield db
