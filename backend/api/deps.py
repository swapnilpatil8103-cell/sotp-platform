"""Shared FastAPI dependencies: singleton clients and Bearer-token (JWT) auth.

Authentication
--------------
Every router except ``/health`` is mounted with ``get_current_user`` (see
``backend/api/main.py``), so a new route is protected by default. Clients send
``Authorization: Bearer <JWT>``. Tokens are HS256-signed with
``AUTH_JWT_SECRET`` and must carry:

- ``sub``   -- the principal's identifier. This is the ONLY source of "who did
  this": it becomes ``AssumptionDecision.decided_by``,
  ``ValuationRun.created_by`` and the ``human:<sub>`` audit-log actor. No
  request body can supply an identity.
- ``exp``   -- expiry; tokens without one are rejected.
- ``roles`` -- optional list of strings. ``require_role("analyst")`` gates the
  endpoints that record a human decision or execute a governed run.

Failure modes: no/invalid/expired token -> 401; valid token without the
required role -> 403; ``AUTH_JWT_SECRET`` unset or too short -> 503 (the
server fails closed; it never falls back to letting requests through).

Tokens for real users are issued by ``POST /auth/login``
(``backend/api/routers/auth.py``). ``create_access_token`` is shared by that route,
tests and ``scripts/mint_dev_token.py`` (a CLI/service-account helper).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Callable, Iterable, Optional

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.services.market_data_client import MarketDataClient
from backend.services.sec_client import SECConnector


@lru_cache(maxsize=1)
def get_sec_client() -> SECConnector:
    """Return a singleton SECConnector, constructed lazily so importing this module
    doesn't require SEC_USER_AGENT to be set (only calling this does)."""
    return SECConnector()


@lru_cache(maxsize=1)
def get_market_data_client() -> MarketDataClient:
    """Return a singleton MarketDataClient."""
    return MarketDataClient()


# --------------------------------------------------------------------------
# Authentication
# --------------------------------------------------------------------------

ROLE_ANALYST = "analyst"
JWT_ALGORITHM = "HS256"
MIN_SECRET_LENGTH = 32
# Matches the column widths of AssumptionDecision.decided_by and
# ValuationRun.created_by, which the subject is written into.
MAX_SUBJECT_LENGTH = 256
DEFAULT_TOKEN_LIFETIME = timedelta(hours=8)


class AuthConfigError(RuntimeError):
    """AUTH_JWT_SECRET is missing or too short to be a usable signing key."""


@dataclass(frozen=True)
class AuthenticatedUser:
    subject: str
    roles: frozenset[str]

    def has_role(self, role: str) -> bool:
        return role in self.roles


def _jwt_secret() -> str:
    # Read per call (not at import) so the process picks up its environment
    # as configured, and tests can set it before the first request.
    secret = os.environ.get("AUTH_JWT_SECRET", "")
    if len(secret) < MIN_SECRET_LENGTH:
        raise AuthConfigError(
            f"AUTH_JWT_SECRET must be set to at least {MIN_SECRET_LENGTH} characters "
            "(generate one with: python -c \"import secrets; print(secrets.token_urlsafe(48))\")."
        )
    return secret


def create_access_token(
    subject: str,
    roles: Iterable[str] = (),
    expires_in: timedelta = DEFAULT_TOKEN_LIFETIME,
) -> str:
    """Mint a signed token for ``subject`` with ``roles``. Raises
    ``AuthConfigError`` if the signing secret is not configured."""
    now = datetime.now(timezone.utc)
    claims = {
        "sub": subject,
        "roles": sorted(set(roles)),
        "iat": now,
        "exp": now + expires_in,
    }
    return jwt.encode(claims, _jwt_secret(), algorithm=JWT_ALGORITHM)


_bearer_scheme = HTTPBearer(auto_error=False)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
) -> AuthenticatedUser:
    """Authenticate the request from its Bearer token, or raise 401/503."""
    if credentials is None:
        raise _unauthorized("Not authenticated: send an 'Authorization: Bearer <token>' header.")

    try:
        secret = _jwt_secret()
    except AuthConfigError:
        # Fail closed. The detail deliberately says nothing about the secret.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is not configured on the server.",
        )

    try:
        claims = jwt.decode(
            credentials.credentials,
            secret,
            algorithms=[JWT_ALGORITHM],  # explicit: never trust the token's own "alg" header
            options={"require": ["exp", "sub"]},
        )
    except jwt.ExpiredSignatureError:
        raise _unauthorized("Token has expired.")
    except jwt.InvalidTokenError:
        raise _unauthorized("Invalid token.")

    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject.strip() or len(subject) > MAX_SUBJECT_LENGTH:
        raise _unauthorized("Invalid token.")

    roles = claims.get("roles", [])
    if not isinstance(roles, list) or not all(isinstance(r, str) for r in roles):
        raise _unauthorized("Invalid token.")

    return AuthenticatedUser(subject=subject, roles=frozenset(roles))


def require_role(role: str) -> Callable[[AuthenticatedUser], AuthenticatedUser]:
    """Build a dependency that authenticates, then requires ``role`` (else 403)."""

    def _dependency(user: AuthenticatedUser = Depends(get_current_user)) -> AuthenticatedUser:
        if not user.has_role(role):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Forbidden: this endpoint requires the '{role}' role.",
            )
        return user

    return _dependency


require_analyst = require_role(ROLE_ANALYST)
