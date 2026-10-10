"""Password hashing, user lookup, login and password change.

Passwords are hashed with Argon2id (``argon2-cffi`` defaults, which follow the
RFC 9106 low-memory profile). Only the encoded hash is stored.

Login hardening:
- Unknown, inactive and wrong-password failures are indistinguishable to the
  caller (same message) and cost about the same time (a dummy hash is verified
  when the user does not exist), so the endpoint is not a user-enumeration oracle.
- Failures are throttled per (email, ip), per ip and per email by the shared,
  database-backed counters in ``backend.services.login_throttle``. A locked
  caller is rejected before any password work and without recording the attempt.

Sessions: ``token_version`` on the user row is embedded in every token. Changing
the password or an admin revoking sessions bumps it, which invalidates every
token issued earlier (see ``backend.api.deps.get_current_user``).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlmodel import Session, select

from backend.models.app_user import AppUser
from backend.services import login_throttle
from backend.services.login_throttle import ThrottledError  # re-exported for callers

__all__ = ["ThrottledError"]

ROLE_ANALYST = "analyst"
ROLE_ADMIN = "admin"
ALLOWED_ROLES = frozenset({ROLE_ANALYST, ROLE_ADMIN})  # a typo must not silently create a powerless role

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 1024  # bound the work an unauthenticated caller can request

_hasher = PasswordHasher()
_dummy_hash: Optional[str] = None


class UserError(ValueError):
    """Invalid request to create/modify a user (e.g. weak password, duplicate)."""


class DuplicateUserError(UserError):
    """A user with that email already exists."""


class WrongPasswordError(UserError):
    """The supplied current password is not the user's password."""


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


def validate_roles(roles) -> list[str]:
    """Normalise a role collection (dedupe, sort) and reject unknown names."""
    cleaned = sorted({str(r).strip() for r in roles if str(r).strip()})
    unknown = [r for r in cleaned if r not in ALLOWED_ROLES]
    if unknown:
        raise UserError(f"Unknown role(s) {unknown}; allowed roles are {sorted(ALLOWED_ROLES)}.")
    return cleaned


def check_password_policy(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise UserError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise UserError(f"Password must be at most {MAX_PASSWORD_LENGTH} characters.")


def create_user(
    session: Session,
    email: str,
    password: str,
    roles: list[str] | tuple[str, ...] = (),
    *,
    commit: bool = True,
) -> AppUser:
    """Create a user. With ``commit=False`` the row is only flushed (it has an id),
    so the caller can add an audit entry and commit both together."""
    email = normalize_email(email)
    if "@" not in email or len(email) > 254:
        raise UserError("A valid email address is required.")
    check_password_policy(password)
    cleaned_roles = validate_roles(roles)
    if session.exec(select(AppUser).where(AppUser.email == email)).first() is not None:
        raise DuplicateUserError(f"A user with email {email} already exists.")
    user = AppUser(email=email, password_hash=hash_password(password), roles=",".join(cleaned_roles))
    session.add(user)
    if commit:
        session.commit()
        session.refresh(user)
    else:
        session.flush()
    return user


def get_user(session: Session, email: str) -> Optional[AppUser]:
    return session.exec(select(AppUser).where(AppUser.email == normalize_email(email))).first()


def authenticate(
    session: Session,
    email: str,
    password: str,
    *,
    ip: str,
    secret: str,
    now: Optional[datetime] = None,
) -> Optional[AppUser]:
    """Return the active user whose credentials match, else ``None``.

    Raises ``ThrottledError`` when the caller is locked out (nothing is recorded
    for a rejected, locked attempt).
    """
    global _dummy_hash
    email = normalize_email(email)
    ehash = login_throttle.email_hash(email, secret)
    login_throttle.check(session, ehash, ip, now)

    user = get_user(session, email)
    if user is None:
        if _dummy_hash is None:
            _dummy_hash = hash_password("dummy-password-for-timing")
        verify_password(password, _dummy_hash)  # equalise timing with the real path
        login_throttle.record_failure(session, ehash, ip, now)
        return None

    ok = verify_password(password, user.password_hash)
    if not ok or not user.is_active:
        login_throttle.record_failure(session, ehash, ip, now)
        return None

    login_throttle.record_success(session, ehash, ip, now)
    if _hasher.check_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    user.last_login_at = datetime.now(timezone.utc)
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def change_password(
    session: Session,
    user: AppUser,
    current_password: str,
    new_password: str,
    *,
    ip: str,
    secret: str,
    now: Optional[datetime] = None,
) -> AppUser:
    """Replace the user's password and invalidate all their existing tokens.

    The current password is required, and a wrong one counts toward the same
    throttle as a failed login (a stolen token must not become a free oracle for
    guessing the password). Raises ``ThrottledError``, ``WrongPasswordError`` or
    ``UserError`` (policy, or reuse of the current password).
    """
    ehash = login_throttle.email_hash(user.email, secret)
    login_throttle.check(session, ehash, ip, now)
    if not verify_password(current_password, user.password_hash):
        login_throttle.record_failure(session, ehash, ip, now)
        raise WrongPasswordError("Current password is incorrect.")
    check_password_policy(new_password)
    if verify_password(new_password, user.password_hash):
        raise UserError("The new password must be different from the current one.")

    user.password_hash = hash_password(new_password)
    user.token_version += 1
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def revoke_sessions(session: Session, user: AppUser) -> AppUser:
    """Invalidate every token issued to ``user`` so far."""
    user.token_version += 1
    session.add(user)
    session.commit()
    session.refresh(user)
    return user
