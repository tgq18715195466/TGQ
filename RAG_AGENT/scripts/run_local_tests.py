"""Run tests with the ignored local .env DSN without exposing credentials."""
import os
import subprocess
import sys
from app.config import settings

if __name__ == '__main__':
    env = dict(os.environ, TEST_POSTGRES_DSN=settings().postgres_dsn)
    raise SystemExit(subprocess.call([sys.executable, '-m', 'pytest', *sys.argv[1:]], env=env))
