"""Local password credentials, separate from tenant-scoped business users."""
from collections import OrderedDict, deque
import hashlib
import hmac
import re
import secrets
from threading import Lock
import time

from sqlalchemy import select

from app.models import LoginCredential


AUTH_TENANT_ID = '00000000-0000-0000-0000-000000000000'
ITERATIONS = 600_000
USERNAME = re.compile(r'[a-z0-9._-]{3,64}\Z')


class LoginRateLimiter:
    """Bound expensive password checks per source in a single API process."""

    def __init__(self, limit: int = 12, window_seconds: int = 60):
        self.limit = limit
        self.window_seconds = window_seconds
        self._attempts: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = Lock()

    def allow(self, source: str) -> bool:
        current = time.monotonic()
        with self._lock:
            attempts = self._attempts.pop(source, deque())
            while attempts and attempts[0] <= current - self.window_seconds:
                attempts.popleft()
            allowed = len(attempts) < self.limit
            if allowed:
                attempts.append(current)
            self._attempts[source] = attempts
            if len(self._attempts) > 2048:
                self._attempts.popitem(last=False)
            return allowed


def normalize_username(value: str) -> str:
    normalized = value.strip().lower()
    if not USERNAME.fullmatch(normalized):
        raise ValueError('Username must be 3-64 letters, numbers, dots, dashes or underscores')
    return normalized


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    if not 12 <= len(password) <= 1024:
        raise ValueError('Password must be 12-1024 characters')
    salt = salt or secrets.token_bytes(32)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, ITERATIONS)
    return f'pbkdf2_sha256${ITERATIONS}${salt.hex()}${digest.hex()}'


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds, salt, expected = encoded.split('$')
        if algorithm != 'pbkdf2_sha256' or int(rounds) != ITERATIONS:
            return False
        actual = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), bytes.fromhex(salt), ITERATIONS)
        return hmac.compare_digest(actual, bytes.fromhex(expected))
    except (TypeError, ValueError):
        return False


def set_login_password(db, username: str, password: str, subject_tenant_id: str, user_id: str) -> LoginCredential:
    normalized = normalize_username(username)
    encoded = hash_password(password)
    credential = db.scalar(select(LoginCredential).where(LoginCredential.tenant_id == AUTH_TENANT_ID,
                                                          LoginCredential.username == normalized))
    if credential and (credential.subject_tenant_id != subject_tenant_id or credential.user_id != user_id):
        raise ValueError('Username is already assigned to another user')
    if credential is None:
        credential = LoginCredential(tenant_id=AUTH_TENANT_ID, username=normalized,
                                     subject_tenant_id=subject_tenant_id, user_id=user_id)
        db.add(credential)
    credential.password_hash = encoded
    credential.failed_attempts = 0
    credential.locked_until = None
    db.flush()
    return credential
