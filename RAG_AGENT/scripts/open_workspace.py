"""Open the local username/password login page."""
import webbrowser

import httpx

from app.config import settings


def main():
    config = settings()
    if config.environment != 'development':
        raise SystemExit('This launcher is for ENVIRONMENT=development only.')
    response = httpx.get('http://127.0.0.1:8000/health/ready', timeout=5)
    response.raise_for_status()
    webbrowser.open('http://127.0.0.1:8000/')
    print('Opened http://127.0.0.1:8000/ at the username/password login page.')


if __name__ == '__main__':
    main()
