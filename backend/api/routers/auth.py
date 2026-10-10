"""``POST /auth/login``: exchange email + password for a short-lived Bearer JWT.

This is the only route besides ``/health`` that does not require a token (it is
mounted without the auth dependency in ``backend/api/main.py``). The token's
``sub`` is the user's email and its ``roles`` come from the ``app_user`` row, so
everything downstream (role gates, ``decided_by``, the audit trail) keeps working
unchanged.
"""

from __future__ import annotations

import os
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session

from backend.api.deps import AuthConfigError, create_access_token
from backend.db import get_session
from backend.services import users

router = APIRouter(prefix="/auth", tags=["auth"])

DEFAULT_TOKEN_MINUTES = 60


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


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, session: Session = Depends(get_session)):
    try:
        user = users.authenticate(session, payload.email, payload.password)
    except users.ThrottledError as exc:
        raise HTTPException(
            status_code=429,
            detail="Too many failed login attempts. Try again later.",
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc
    if user is None:
        # Identical for unknown email, wrong password and disabled account.
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    roles = users.parse_roles(user.roles)
    lifetime = token_lifetime()
    try:
        token = create_access_token(user.email, roles, expires_in=lifetime)
    except AuthConfigError:
        raise HTTPException(status_code=503, detail="Authentication is not configured on the server.")
    return LoginResponse(
        access_token=token,
        expires_in=int(lifetime.total_seconds()),
        subject=user.email,
        roles=roles,
    )
