"""Create isolated project database/role only; never modifies an existing database.

Set PGADMIN_PASSWORD in the process environment. Secret values are never printed.
"""
import os
from pathlib import Path
import secrets

import psycopg
from psycopg import sql


def main():
    role, database = 'zhanggui_app', 'zhanggui_dev'
    env_file = Path('.env')
    if env_file.exists():
        raise SystemExit('Existing .env preserved. Bootstrap was not run.')
    password = secrets.token_hex(24)
    with psycopg.connect(host='127.0.0.1', user='postgres', dbname='postgres',
                         password=os.environ['PGADMIN_PASSWORD'], autocommit=True) as conn:
        if conn.execute('SELECT 1 FROM pg_database WHERE datname = %s', (database,)).fetchone():
            raise SystemExit('Existing project database preserved. No changes made.')
        if conn.execute('SELECT 1 FROM pg_roles WHERE rolname = %s', (role,)).fetchone():
            raise SystemExit('Existing project role preserved. No changes made.')
        conn.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS')
                     .format(sql.Identifier(role), sql.Literal(password)))
        conn.execute(sql.SQL('CREATE DATABASE {} OWNER {}').format(sql.Identifier(database), sql.Identifier(role)))
    dsn = f'postgresql+psycopg://{role}:{password}@127.0.0.1:5432/{database}'
    env_file.write_text(f'POSTGRES_DSN={dsn}\nJWT_SECRET={secrets.token_hex(32)}\n'
                        'ENVIRONMENT=development\nDEVELOPMENT_SKIP_SCANNER=false\n', encoding='utf-8')
    print(f'Created isolated database {database} and restricted role {role}; credentials stored in ignored .env.')


if __name__ == '__main__':
    main()
