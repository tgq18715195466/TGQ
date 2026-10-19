from alembic import context
from sqlalchemy import create_engine, pool, text
from sqlalchemy.engine import make_url

from app.config import settings
from app.models import Base
from app.database_target import validate_target

target = make_url(settings().postgres_dsn)
validate_target(target.database, target.username)

if context.is_offline_mode():
    context.configure(url=settings().postgres_dsn, target_metadata=Base.metadata,
                      literal_binds=True, dialect_opts={'paramstyle': 'named'})
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(settings().postgres_dsn, poolclass=pool.NullPool)
    with engine.connect() as connection:
        validate_target(*connection.execute(text(
            'SELECT current_database(), current_user, rolsuper FROM pg_roles WHERE rolname=current_user'
        )).one())
        connection.commit()
        context.configure(connection=connection, target_metadata=Base.metadata)
        with context.begin_transaction():
            context.run_migrations()
