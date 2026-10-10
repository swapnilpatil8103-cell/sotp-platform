"""One row per login (or password-change) attempt, backing the shared throttle."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class LoginAttempt(SQLModel, table=True):
    __tablename__ = "login_attempt"

    id: Optional[int] = Field(default=None, primary_key=True)
    # HMAC-SHA256 (keyed by AUTH_JWT_SECRET) of the normalised email, hex. The
    # table never holds an email address, and records attempts for unknown
    # emails too, so it reveals nothing about which accounts exist.
    email_hash: str = Field(max_length=64, index=True)
    ip: str = Field(max_length=45, index=True)
    # Naive UTC.
    ts: datetime = Field(index=True)
    success: bool = Field(default=False)
