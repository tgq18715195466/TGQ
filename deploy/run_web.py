"""Assemble the container-only PostgreSQL URI without leaking or misquoting secrets."""
import os
import sys
from urllib.parse import quote


def postgres_uri(environ):
    user = quote(environ["POSTGRES_USER"], safe="")
    password = quote(environ["POSTGRES_PASSWORD"], safe="")
    database = quote(environ["POSTGRES_DB"], safe="")
    host = environ.get("POSTGRES_HOST", "postgres")
    port = int(environ.get("POSTGRES_PORT", "5432"))
    return f"postgresql://{user}:{password}@{host}:{port}/{database}?connect_timeout=5"


def main():
    os.environ["POSTGRES_URI"] = postgres_uri(os.environ)
    os.execvp(sys.executable, [sys.executable, "-m", "uvicorn", "api_view.web_main:app",
                              "--host", "0.0.0.0", "--port", "8090", "--workers", "1"])


if __name__ == "__main__":
    main()
