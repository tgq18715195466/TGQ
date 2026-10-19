"""Create framework checkpoint tables and force tenant isolation of thread IDs."""
import psycopg
from psycopg.rows import dict_row
from langgraph.checkpoint.postgres import PostgresSaver
from sqlalchemy.engine import make_url
from app.config import settings
from app.database_target import validate_target


def main():
    config = settings()
    url = make_url(config.checkpoint_dsn or config.postgres_dsn).set(drivername='postgresql')
    validate_target(url.database, url.username)
    with psycopg.connect(url.render_as_string(hide_password=False), autocommit=True,
                         row_factory=dict_row) as conn:
        identity = conn.execute('SELECT current_database() AS db, current_user AS usr, rolsuper '
                                'FROM pg_roles WHERE rolname=current_user').fetchone()
        validate_target(identity['db'], identity['usr'], identity['rolsuper'])
        conn.execute('CREATE SCHEMA IF NOT EXISTS workflow')
        conn.execute('SET search_path TO workflow')
        PostgresSaver(conn).setup()
        for table in ['checkpoints', 'checkpoint_blobs', 'checkpoint_writes']:
            conn.execute(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY')
            conn.execute(f'ALTER TABLE {table} FORCE ROW LEVEL SECURITY')
            conn.execute(f'DROP POLICY IF EXISTS tenant_isolation ON {table}')
            conn.execute(f'''CREATE POLICY tenant_isolation ON {table}
                USING (split_part(thread_id, ':', 1) = current_setting('app.tenant_id', true))
                WITH CHECK (split_part(thread_id, ':', 1) = current_setting('app.tenant_id', true))''')
    print('PostgreSQL checkpoint tables initialized with forced tenant policies.')


if __name__ == '__main__':
    main()
