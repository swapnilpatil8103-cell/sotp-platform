"""Token helpers for tests. ``conftest.py`` pins ``AUTH_JWT_SECRET`` to
``TEST_JWT_SECRET`` for the whole session, so tokens minted here always verify
against the app under test regardless of the developer's real ``.env``.

The app loads the user row on every request and takes roles from it (the token's
own roles are ignored), so a token is only useful if its subject exists in the
test database. ``TEST_ROSTER`` is seeded into every test's database by
``conftest.py``; the ``roles`` argument of ``auth_headers`` is informational.
Tests that need a different roster (deactivated or demoted users) change the rows."""

from __future__ import annotations

from datetime import timedelta
from typing import Iterable

from backend.api.deps import ROLE_ANALYST, create_access_token

TEST_JWT_SECRET = "test-only-signing-secret-not-for-real-use-0123456789"

DEFAULT_SUBJECT = "analyst@firm.com"

# email -> roles, as stored in app_user.roles
TEST_ROSTER: dict[str, str] = {
    "analyst@firm.com": ROLE_ANALYST,
    "alice@firm.com": ROLE_ANALYST,
    "bob@firm.com": ROLE_ANALYST,
    "proposer@firm.com": ROLE_ANALYST,
    "viewer@firm.com": "",
    "v@firm.com": "",
}


def seed_test_roster(session) -> None:
    """Insert the roster. Hashes are placeholders: these accounts never log in
    (a placeholder never verifies), they only own tokens minted by ``auth_headers``."""
    from backend.models.app_user import AppUser

    for email, roles in TEST_ROSTER.items():
        session.add(AppUser(email=email, password_hash="!not-a-real-hash", roles=roles))
    session.commit()


def auth_headers(
    subject: str = DEFAULT_SUBJECT,
    roles: Iterable[str] = (ROLE_ANALYST,),
    expires_in: timedelta = timedelta(hours=1),
) -> dict[str, str]:
    """``Authorization`` header for ``subject`` (an analyst by default)."""
    return {"Authorization": f"Bearer {create_access_token(subject, roles, expires_in)}"}
