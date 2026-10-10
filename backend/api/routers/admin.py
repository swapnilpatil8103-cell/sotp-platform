"""Admin-only user administration. There is no public sign-up: accounts exist only
because an admin created them here or ``scripts/create_user.py`` did.

Every route requires the ``admin`` role (a router-level dependency, so a route
added later is covered too). Every change is audited with the acting admin as
actor; see ``backend/services/user_admin.py`` for the rules (last-admin guard,
atomic audit, deactivation revokes sessions).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlmodel import Session

from backend.api.deps import AuthenticatedUser, require_admin
from backend.audit.logger import get_audit_trail
from backend.db import get_session
from backend.models.app_user import AppUser
from backend.services import user_admin, users

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


class UserOut(BaseModel):
    """Never includes the password hash."""

    id: int
    email: str
    roles: list[str]
    is_active: bool
    token_version: int
    created_at: datetime
    last_login_at: Optional[datetime]

    @classmethod
    def of(cls, user: AppUser) -> "UserOut":
        return cls(
            id=user.id,
            email=user.email,
            roles=users.parse_roles(user.roles),
            is_active=user.is_active,
            token_version=user.token_version,
            created_at=user.created_at,
            last_login_at=user.last_login_at,
        )


class UserList(BaseModel):
    total: int
    users: list[UserOut]


def _check_roles(value: Optional[list[str]]) -> Optional[list[str]]:
    if value is None:
        return None
    try:
        return users.validate_roles(value)
    except users.UserError as exc:
        raise ValueError(str(exc)) from exc


class CreateUserRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(..., min_length=3, max_length=254)
    password: str = Field(
        ..., min_length=users.MIN_PASSWORD_LENGTH, max_length=users.MAX_PASSWORD_LENGTH,
        description="Initial password; the user should change it at once (POST /auth/change-password).",
    )
    roles: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("roles")
    @classmethod
    def _roles(cls, v: list[str]) -> list[str]:
        return _check_roles(v) or []


class UpdateUserRequest(BaseModel):
    """Any combination of the fields; at least one is required. ``roles`` replaces the whole list."""

    model_config = ConfigDict(extra="forbid")

    roles: Optional[list[str]] = Field(None, max_length=8)
    is_active: Optional[bool] = None
    revoke_sessions: Optional[bool] = Field(None, description="true: invalidate all of the user's tokens")

    @field_validator("roles")
    @classmethod
    def _roles(cls, v: Optional[list[str]]) -> Optional[list[str]]:
        return _check_roles(v)

    @model_validator(mode="after")
    def _something_to_do(self) -> "UpdateUserRequest":
        if self.roles is None and self.is_active is None and self.revoke_sessions is None:
            raise ValueError("Provide at least one of roles, is_active, revoke_sessions.")
        return self


@router.get("/users", response_model=UserList)
def list_users(
    is_active: Optional[bool] = Query(None, description="Filter by active flag"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
):
    rows, total = user_admin.list_users(session, is_active=is_active, limit=limit, offset=offset)
    return UserList(total=total, users=[UserOut.of(u) for u in rows])


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(
    payload: CreateUserRequest,
    admin: AuthenticatedUser = Depends(require_admin),
    session: Session = Depends(get_session),
):
    try:
        user = user_admin.create(
            session, actor=admin.subject, email=payload.email, password=payload.password, roles=payload.roles
        )
    except users.DuplicateUserError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except users.UserError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return UserOut.of(user)


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(
    user_id: int,
    payload: UpdateUserRequest,
    admin: AuthenticatedUser = Depends(require_admin),
    session: Session = Depends(get_session),
):
    try:
        user = user_admin.update(
            session,
            actor=admin.subject,
            user_id=user_id,
            roles=payload.roles,
            is_active=payload.is_active,
            revoke_sessions=bool(payload.revoke_sessions),
        )
    except user_admin.UserNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except user_admin.LastAdminError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except users.UserError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return UserOut.of(user)


@router.get("/users/{user_id}/audit")
def user_audit_trail(user_id: int, session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    """Chronological administrative history of one account (oldest first)."""
    try:
        user_admin.get(session, user_id)
    except user_admin.UserNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return get_audit_trail(session, entity_type=user_admin.ENTITY_TYPE, entity_id=user_id)
