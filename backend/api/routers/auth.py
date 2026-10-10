"""Authentication routes.

``POST /auth/login`` is the only route besides ``/health`` that does not require
a token (it is mounted without the auth dependency in ``backend/api/main.py``).
The token's ``sub`` is the user's email, ``roles`` come from the ``app_user`` row
and ``tv`` is the row's ``token_version``, so everything downstream (role gates,
``decided_by``, the audit trail) keeps working unchanged.

``POST /auth/change-password`` and ``POST /auth/revoke-sessions`` authenticate
themselves with the normal Bearer dependency. Both bump ``token_version``, which
invalidates every token issued before the change.
"""

from __future__ import annotations

import os
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session

from backend.api.deps import (
    AuthConfigError,
    AuthenticatedUser,
    _jwt_secret,
    client_ip,
    create_access_token,
    get_current_user,
    require_admin,
)
from backend.db import get_session
from backend.services import users

router = APIRouter(prefix="/auth", tags=["auth"])

DEFAULT_TOKEN_MINUTES = 60
_NOT_CONFIGURED = "Authentication is not configured on the server."


def token_lifetime() -> timedelta:
    try:
        minutes = int(os.environ.get("AUTH_TOKEN_MINUTES", DEFAULT_TOKEN_MINUTES))
    except ValueError:
        minutes = DEFAULT_TOKEN_MINUTES
    return timedelta(minutes=min(max(minutes, 1), 24 * 60))


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(..., min_length=3, max_length=254)
    password: str = Field(..., min_length=1, max_length=users.MAX_PASSWORD_LENGTH)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(..., description="Seconds until the token expires")
    subject: str
    roles: list[str]


class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(..., min_length=1, max_length=users.MAX_PASSWORD_LENGTH)
    new_password: str = Field(..., min_length=users.MIN_PASSWORD_LENGTH, max_length=users.MAX_PASSWORD_LENGTH)


class RevokeSessionsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(..., min_length=3, max_length=254)


class RevokeSessionsResponse(BaseModel):
    email: str
    token_version: int


def _issue(user) -> LoginResponse:
    roles = users.parse_roles(user.roles)
    lifetime = token_lifetime()
    token = create_access_token(user.email, roles, expires_in=lifetime, token_version=user.token_version)
    return LoginResponse(access_token=token, expires_in=int(lifetime.total_seconds()), subject=user.email, roles=roles)


def _throttled(exc: users.ThrottledError) -> HTTPException:
    return HTTPException(
        status_code=429,
        detail="Too many failed attempts. Try again later.",
        headers={"Retry-After": str(exc.retry_after)},
    )


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, request: Request, session: Session = Depends(get_session)):
    try:
        secret = _jwt_secret()
    except AuthConfigError:
        raise HTTPException(status_code=503, detail=_NOT_CONFIGURED)
    try:
        user = users.authenticate(session, payload.email, payload.password, ip=client_ip(request), secret=secret)
    except users.ThrottledError as exc:
        raise _throttled(exc) from exc
    if user is None:
        # Identical for unknown email, wrong password and disabled account.
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    return _issue(user)


@router.post("/change-password", response_model=LoginResponse)
def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    current: AuthenticatedUser = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Change the caller's password. Requires the current one; the new one must
    meet the length policy and differ from it. Invalidates every existing token
    (including this one) and returns a fresh token so the caller stays signed in."""
    try:
        secret = _jwt_secret()
    except AuthConfigError:
        raise HTTPException(status_code=503, detail=_NOT_CONFIGURED)
    user = users.get_user(session, current.subject)
    if user is None:  # deleted between get_current_user and here
        raise HTTPException(status_code=401, detail="Account is no longer active.")
    try:
        user = users.change_password(
            session, user, payload.current_password, payload.new_password, ip=client_ip(request), secret=secret
        )
    except users.ThrottledError as exc:
        raise _throttled(exc) from exc
    except users.WrongPasswordError as exc:
        # 403, not 401: a 401 would make clients treat the session as expired.
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except users.UserError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _issue(user)


@router.post("/revoke-sessions", response_model=RevokeSessionsResponse)
def revoke_sessions(
    payload: RevokeSessionsRequest,
    _admin: AuthenticatedUser = Depends(require_admin),
    session: Session = Depends(get_session),
):
    """Admin only: sign ``email`` out everywhere by invalidating all of their
    existing tokens. Revoking yourself is allowed (you will need to sign in again)."""
    user = users.get_user(session, payload.email)
    if user is None:
        raise HTTPException(status_code=404, detail="No such user.")
    user = users.revoke_sessions(session, user)
    return RevokeSessionsResponse(email=user.email, token_version=user.token_version)
