"""Mint a Bearer token for an EXISTING user, for scripts and curl.

Real users sign in with ``POST /auth/login`` (create one with
``scripts/create_user.py``). This is a shortcut for automation: it signs a token
for a user who already exists and is active, without needing their password. It
needs ``AUTH_JWT_SECRET`` and ``DATABASE_URL`` from the environment or the repo's
``.env``, and it is as powerful as that secret, so keep it off shared machines.

The API loads the user row on every request and takes roles from it, so the
token carries no authority of its own: deactivating the user or removing their
role stops it working immediately.

    python scripts/mint_dev_token.py --sub you@example.com
    python scripts/mint_dev_token.py --sub you@example.com --hours 720

Only the token is written to stdout, so it can be redirected or captured.
"""

from __future__ import annotations

import argparse
import sys
from datetime import timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(REPO_ROOT / ".env")

from sqlmodel import Session, select  # noqa: E402

from backend.api.deps import AuthConfigError, create_access_token  # noqa: E402
from backend.db import get_engine  # noqa: E402
from backend.models.app_user import AppUser  # noqa: E402
from backend.services import users  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sub", required=True, help="Email of an existing, active user (recorded as the approver / run author).")
    parser.add_argument("--hours", type=float, default=8.0, help="Token lifetime in hours (default 8).")
    args = parser.parse_args()

    with Session(get_engine()) as session:
        user = session.exec(select(AppUser).where(AppUser.email == users.normalize_email(args.sub))).first()
    if user is None or not user.is_active:
        print(
            f"error: no active user {args.sub!r}. Create one with scripts/create_user.py first.",
            file=sys.stderr,
        )
        return 1

    try:
        token = create_access_token(user.email, users.parse_roles(user.roles), timedelta(hours=args.hours))
    except AuthConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
