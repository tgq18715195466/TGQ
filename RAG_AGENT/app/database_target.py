"""Fail closed before migrations when a connection targets another project."""
import os


def validate_target(database, user, superuser=False):
    expected_database = os.environ.get('POSTGRES_EXPECTED_DATABASE', 'zhanggui_dev')
    expected_user = os.environ.get('POSTGRES_EXPECTED_USER', 'zhanggui_app')
    if database != expected_database or user != expected_user or superuser:
        raise RuntimeError('Database target mismatch or privileged application role; migration refused')
