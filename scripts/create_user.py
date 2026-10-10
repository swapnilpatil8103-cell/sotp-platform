"""Create (or reset the password of) a login user. There is no public sign-up.

    python scripts/create_user.py --email you@example.com --roles analyst
    python scripts/create_user.py --email you@example.com --reset-password
    python scripts/create_user.py --email you@example.com --revoke-sessions

The password is read from a hidden prompt (or the SOTP_NEW_PASSWORD env var for
automation), never from the command line, so it does not land in shell history.
Uses DATABASE_URL from the environment or the repo's .env.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(REPO_ROOT / ".env")

from sqlmodel import Session, select  # noqa: E402

from backend.db import get_engine  # noqa: E402
from backend.models.app_user import AppUser  # noqa: E402
from backend.services import users  # noqa: E402


def _read_password() -> str:
    env = os.environ.get("SOTP_NEW_PASSWORD")
    if env:
        return env
    first = getpass.getpass("Password: ")
    if first != getpass.getpass("Repeat password: "):
        raise SystemExit("Passwords do not match.")
    return first


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--email", required=True)
    parser.add_argument("--roles", default="analyst", help="comma-separated; empty for a read-only user (default: analyst)")
    parser.add_argument("--reset-password", action="store_true", help="change the password of an existing user")
    parser.add_argument("--revoke-sessions", action="store_true", help="invalidate all existing tokens of a user")
    args = parser.parse_args()

    if args.revoke_sessions:
        with Session(get_engine()) as session:
            user = users.get_user(session, args.email)
            if user is None:
                raise SystemExit(f"No user {args.email}.")
            users.revoke_sessions(session, user)
            print(f"Revoked all sessions for {user.email} (token_version={user.token_version}).")
        return 0

    password = _read_password()
    roles = users.parse_roles(args.roles)
    with Session(get_engine()) as session:
        try:
            if args.reset_password:
                user = session.exec(select(AppUser).where(AppUser.email == users.normalize_email(args.email))).first()
                if user is None:
                    raise SystemExit(f"No user {args.email}.")
                if not (users.MIN_PASSWORD_LENGTH <= len(password) <= users.MAX_PASSWORD_LENGTH):
                    raise SystemExit(f"Password must be {users.MIN_PASSWORD_LENGTH}-{users.MAX_PASSWORD_LENGTH} characters.")
                user.password_hash = users.hash_password(password)
                user.token_version += 1  # a reset must also end the old sessions
                session.add(user)
                session.commit()
                print(f"Password updated for {user.email}.")
            else:
                user = users.create_user(session, args.email, password, roles)
                print(f"Created {user.email} with roles: {', '.join(roles) or '(none)'}.")
        except users.UserError as exc:
            raise SystemExit(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
