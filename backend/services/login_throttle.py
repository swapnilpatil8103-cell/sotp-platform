"""Shared, database-backed login throttle.

Attempts live in the ``login_attempt`` table, so the limits survive restarts and
are shared by every worker/instance (the old in-memory counter was neither).

Three independent counters over a rolling ``WINDOW`` of failures:

=========  =========================  =========  ==========  =========
scope      key                        threshold  base lock   max lock
=========  =========================  =========  ==========  =========
pair       (email, ip)                5          60 s        15 min
ip         ip, across all emails      20         60 s        15 min
email      email, across all ips      10         60 s        5 min
=========  =========================  =========  ==========  =========

Once a scope has ``threshold`` failures in the window, the caller is locked out
for ``base * 2**(failures - threshold)`` seconds after the latest failure, capped
(exponential back-off). Rules that stop the throttle from becoming a weapon:

* **Attempts made while locked are rejected without being recorded**, so
  hammering a locked account cannot extend the lock. A lock lasts at most its cap
  after the last *recorded* failure.
* **The pair scope is the tight one.** Someone guessing a victim's password from
  their own IP locks only (victim, attacker-ip); the victim, logging in from
  their usual address, is unaffected.
* **The email-wide scope is bypassed for an IP that has already logged in
  successfully as that user** (within ``TRUSTED_IP_DAYS``). So an attacker who
  spreads failures across many addresses to trip the email-wide limit still cannot
  lock the real user out of the places they actually sign in from.
* A success clears that (email, ip) pair's failures.

Rows older than the window (failures) or ``TRUSTED_IP_DAYS`` (successes) are
deleted opportunistically.

Limits of this design: a determined attacker with many IPs gets about
``EMAIL.threshold`` guesses per window per email (plus back-off), which is why
passwords have a 12-character minimum and are hashed with Argon2id. IPs come from
``backend.api.deps``'s ``client_ip`` and are only as trustworthy as the proxy
configuration (``AUTH_TRUST_FORWARDED_FOR``): if a client can set its own
``X-Forwarded-For`` (the Next.js server passes it through unchanged), it can pick
its own address and so escape the pair and ip scopes, leaving only the email scope.
Deploy behind a reverse proxy that appends the real peer address.
"""

from __future__ import annotations

import hashlib
import hmac
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import delete, func
from sqlmodel import Session, select

from backend.models.login_attempt import LoginAttempt

WINDOW = timedelta(minutes=15)
TRUSTED_IP_DAYS = 30
MAX_EXPONENT = 16  # keeps 2**n small; the cap is what actually bounds the lock


@dataclass(frozen=True)
class Scope:
    name: str
    threshold: int
    base_seconds: int
    cap_seconds: int


PAIR = Scope("pair", threshold=5, base_seconds=60, cap_seconds=15 * 60)
IP = Scope("ip", threshold=20, base_seconds=60, cap_seconds=15 * 60)
EMAIL = Scope("email", threshold=10, base_seconds=60, cap_seconds=5 * 60)


class ThrottledError(RuntimeError):
    def __init__(self, retry_after: int):
        super().__init__("Too many failed login attempts.")
        self.retry_after = retry_after


def utcnow() -> datetime:
    """Naive UTC, matching the ``login_attempt.ts`` column."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def email_hash(email: str, secret: str) -> str:
    return hmac.new(secret.encode(), email.strip().lower().encode(), hashlib.sha256).hexdigest()


def lock_seconds(scope: Scope, failures: int) -> int:
    """Lock length once ``failures`` have occurred (0 below the threshold)."""
    if failures < scope.threshold:
        return 0
    return min(scope.base_seconds * 2 ** min(failures - scope.threshold, MAX_EXPONENT), scope.cap_seconds)


def _failure_times(session: Session, since: datetime, *, ehash: Optional[str] = None, ip: Optional[str] = None) -> list[datetime]:
    stmt = select(LoginAttempt.ts).where(LoginAttempt.success == False, LoginAttempt.ts >= since)  # noqa: E712
    if ehash is not None:
        stmt = stmt.where(LoginAttempt.email_hash == ehash)
    if ip is not None:
        stmt = stmt.where(LoginAttempt.ip == ip)
    return list(session.exec(stmt.order_by(LoginAttempt.ts)).all())


def _remaining(scope: Scope, times: list[datetime], now: datetime) -> float:
    seconds = lock_seconds(scope, len(times))
    if not seconds:
        return 0.0
    return max(0.0, (times[-1] + timedelta(seconds=seconds) - now).total_seconds())


def is_trusted_ip(session: Session, ehash: str, ip: str, now: datetime) -> bool:
    """True if ``ip`` has a successful login for this email within ``TRUSTED_IP_DAYS``."""
    found = session.exec(
        select(func.count())
        .select_from(LoginAttempt)
        .where(
            LoginAttempt.success == True,  # noqa: E712
            LoginAttempt.email_hash == ehash,
            LoginAttempt.ip == ip,
            LoginAttempt.ts >= now - timedelta(days=TRUSTED_IP_DAYS),
        )
    ).one()
    return found > 0


def check(session: Session, ehash: str, ip: str, now: Optional[datetime] = None) -> None:
    """Raise ``ThrottledError`` if any scope is currently locked. Records nothing."""
    now = now or utcnow()
    since = now - WINDOW
    remaining = max(
        _remaining(PAIR, _failure_times(session, since, ehash=ehash, ip=ip), now),
        _remaining(IP, _failure_times(session, since, ip=ip), now),
    )
    email_remaining = _remaining(EMAIL, _failure_times(session, since, ehash=ehash), now)
    if email_remaining and not is_trusted_ip(session, ehash, ip, now):
        remaining = max(remaining, email_remaining)
    if remaining > 0:
        raise ThrottledError(retry_after=max(1, math.ceil(remaining)))


def _cleanup(session: Session, now: datetime) -> None:
    session.exec(
        delete(LoginAttempt).where(
            (LoginAttempt.success == False) & (LoginAttempt.ts < now - WINDOW)  # noqa: E712
            | (LoginAttempt.ts < now - timedelta(days=TRUSTED_IP_DAYS))
        )
    )


def record_failure(session: Session, ehash: str, ip: str, now: Optional[datetime] = None) -> None:
    now = now or utcnow()
    _cleanup(session, now)
    session.add(LoginAttempt(email_hash=ehash, ip=ip, ts=now, success=False))
    session.commit()


def record_success(session: Session, ehash: str, ip: str, now: Optional[datetime] = None) -> None:
    now = now or utcnow()
    _cleanup(session, now)
    # Forgive this (email, ip) pair's failures; other addresses' failures stand.
    session.exec(
        delete(LoginAttempt).where(
            LoginAttempt.email_hash == ehash, LoginAttempt.ip == ip, LoginAttempt.success == False  # noqa: E712
        )
    )
    session.add(LoginAttempt(email_hash=ehash, ip=ip, ts=now, success=True))
    session.commit()
