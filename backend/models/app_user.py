"""Application user for password login (``POST /auth/login``)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AppUser(SQLModel, table=True):
    __tablename__ = "app_user"

    id: Optional[int] = Field(default=None, primary_key=True)
    # Stored lower-cased and stripped; it becomes the token ``sub`` and therefore
    # the identity written to ``decided_by`` / ``created_by`` / the audit log.
    email: str = Field(max_length=254, unique=True, index=True)
    # Argon2id encoded hash (includes salt and parameters). Never the password.
    password_hash: str = Field(max_length=255)
    # Comma-separated role names, e.g. "analyst". Empty = read-only user.
    roles: str = Field(default="", max_length=256)
    is_active: bool = Field(default=True)
    # Embedded in every token as "tv". Bumped by a password change or an admin
    # "revoke sessions", which invalidates all tokens issued before it.
    token_version: int = Field(default=0)
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    last_login_at: Optional[datetime] = Field(default=None)
