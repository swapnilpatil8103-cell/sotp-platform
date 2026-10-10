"""Password hashing, user lookup and login throttling.

Passwords are hashed with Argon2id (``argon2-cffi`` defaults, which follow the
RFC 9106 low-memory profile). Only the encoded hash is stored.

Login hardening:
- Unknown, inactive and wrong-password failures are indistinguishable to the
  caller (same message) and cost about the same time (a dummy hash is verified
  when the user does not exist), so the endpoint is not a user-enumeration oracle.
- Repeated failures for one email are throttled (``MAX_FAILURES`` within
  ``FAILURE_WINDOW``). The counter is in process memory: it resets on restart
  and is not shared between workers. It slows online guessing; it is not a
  substitute for a shared rate limiter in front of a multi-instance deployment.
  A side effect is that someone can temporarily lock a known email out.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Deque, Optional

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlmodel import Session, select

from backend.models.app_user import AppUser

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 1024  # bound the work an unauthenticated caller can request
MAX_FAILURES = 5
FAILURE_WINDOW = 15 * 60  # seconds

_hasher = PasswordHasher()
_dummy_hash: Optional[str] = None
_failures: dict[str, Deque[float]] = defaultdict(deque)
_lock = threading.Lock()


class UserError(ValueError):
    """Invalid request to create/modify a user (e.g. weak password, duplicate)."""


class ThrottledError(RuntimeError):
    def __init__(self, retry_after: int):
        super().__init__("Too many failed login attempts.")
        self.retry_after = retry_after


def normalize_email(email: str) -> str:
    return email.strip().lower()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, encoded_hash: str) -> bool:
    try:
        return _hasher.verify(encoded_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def parse_roles(roles: str) -> list[str]:
    return sorted({r.strip() for r in roles.split(",") if r.strip()})


def create_user(session: Session, email: str, password: str, roles: list[str] | tuple[str, ...] = ()) -> AppUser:
    email = normalize_email(email)
    if "@" not in email or len(email) > 254:
        raise UserError("A valid email address is required.")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise UserError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise UserError(f"Password must be at most {MAX_PASSWORD_LENGTH} characters.")
    if session.exec(select(AppUser).where(AppUser.email == email)).first() is not None:
        raise UserError(f"A user with email {email} already exists.")
    user = AppUser(email=email, password_hash=hash_password(password), roles=",".join(sorted(set(roles))))
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def _check_throttle(email: str, now: float) -> None:
    with _lock:
        window = _failures[email]
        while window and now - window[0] > FAILURE_WINDOW:
            window.popleft()
        if len(window) >= MAX_FAILURES:
            raise ThrottledError(retry_after=max(1, int(FAILURE_WINDOW - (now - window[0]))))


def _record_failure(email: str, now: float) -> None:
    with _lock:
        _failures[email].append(now)


def reset_throttle() -> None:
    """Forget all recorded failures (used by tests)."""
    with _lock:
        _failures.clear()


def authenticate(session: Session, email: str, password: str) -> Optional[AppUser]:
    """Return the active user whose credentials match, else ``None``.

    Raises ``ThrottledError`` when the email has too many recent failures.
    """
    global _dummy_hash
    email = normalize_email(email)
    now = time.monotonic()
    _check_throttle(email, now)

    user = session.exec(select(AppUser).where(AppUser.email == email)).first()
    if user is None:
        if _dummy_hash is None:
            _dummy_hash = hash_password("dummy-password-for-timing")
        verify_password(password, _dummy_hash)  # equalise timing with the real path
        _record_failure(email, now)
        return None

    ok = verify_password(password, user.password_hash)
    if not ok or not user.is_active:
        _record_failure(email, now)
        return None

    with _lock:
        _failures.pop(email, None)
    if _hasher.check_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    user.last_login_at = datetime.now(timezone.utc)
    session.add(user)
    session.commit()
    session.refresh(user)
    return user
