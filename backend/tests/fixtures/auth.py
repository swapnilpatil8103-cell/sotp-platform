"""Token helpers for tests. ``conftest.py`` pins ``AUTH_JWT_SECRET`` to
``TEST_JWT_SECRET`` for the whole session, so tokens minted here always verify
against the app under test regardless of the developer's real ``.env``."""

from __future__ import annotations

from datetime import timedelta
from typing import Iterable

from backend.api.deps import ROLE_ANALYST, create_access_token

TEST_JWT_SECRET = "test-only-signing-secret-not-for-real-use-0123456789"

DEFAULT_SUBJECT = "analyst@firm.com"


def auth_headers(
    subject: str = DEFAULT_SUBJECT,
    roles: Iterable[str] = (ROLE_ANALYST,),
    expires_in: timedelta = timedelta(hours=1),
) -> dict[str, str]:
    """``Authorization`` header for ``subject`` (an analyst by default)."""
    return {"Authorization": f"Bearer {create_access_token(subject, roles, expires_in)}"}
