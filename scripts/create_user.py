"""Create and maintain login users from the command line. There is no public sign-up.

    python scripts/create_user.py --email you@example.com --roles analyst
    python scripts/create_user.py --email you@example.com --bootstrap-admin
    python scripts/create_user.py --email you@example.com --reset-password
    python scripts/create_user.py --email you@example.com --revoke-sessions

--bootstrap-admin creates the FIRST admin (or promotes an existing user to admin)
so someone can use the /admin/users API. It refuses to run once any active admin
exists; from then on, admins manage users through the API. Valid roles: analyst,
admin. Every action is written to the audit log with actor "system".

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

from backend.audit.logger import ACTION_USER_PASSWORD_RESET, ACTOR_SYSTEM, log_event  # noqa: E402
from backend.db import get_engine  # noqa: E402
from backend.models.app_user import AppUser  # noqa: E402
from backend.services import user_admin, users  # noqa: E402

VIA = "scripts/create_user.py"


def _read_password() -> str:
    env = os.environ.get("SOTP_NEW_PASSWORD")
    if env:
        return env
    first = getpass.getpass("Password: ")
    if first != getpass.getpass("Repeat password: "):
        raise SystemExit("Passwords do not match.")
    return first


def _active_admins(session: Session) -> list[AppUser]:
    return [
        u
        for u in session.exec(select(AppUser)).all()
        if u.is_active and users.ROLE_ADMIN in users.parse_roles(u.roles)
    ]


def _bootstrap_admin(session: Session, email: str, extra_roles: list[str]) -> str:
    existing = _active_admins(session)
    if existing:
        raise SystemExit(
            f"An active admin already exists ({existing[0].email}). --bootstrap-admin is only for the "
            "first one; use the admin API (PATCH /admin/users/{id}) to manage users."
        )
    roles = users.validate_roles([users.ROLE_ADMIN, *extra_roles])
    user = users.get_user(session, email)
    if user is None:
        user = user_admin.create(session, actor=None, email=email, password=_read_password(), roles=roles, via=VIA)
        return f"Created first admin {user.email} with roles: {', '.join(roles)}."
    merged = users.validate_roles([*users.parse_roles(user.roles), *roles])
    user_admin.update(session, actor=None, user_id=user.id, roles=merged, is_active=True, via=VIA)
    return f"Promoted existing user {user.email} to admin (roles: {', '.join(merged)})."


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--email", required=True)
    parser.add_argument("--roles", default="analyst", help="comma-separated; empty for a read-only user (default: analyst)")
    parser.add_argument("--bootstrap-admin", action="store_true", help="create the first admin (refused if an active admin exists)")
    parser.add_argument("--reset-password", action="store_true", help="change the password of an existing user")
    parser.add_argument("--revoke-sessions", action="store_true", help="invalidate all existing tokens of a user")
    args = parser.parse_args()

    try:
        roles = users.validate_roles(users.parse_roles(args.roles))
    except users.UserError as exc:
        raise SystemExit(str(exc))

    with Session(get_engine()) as session:
        try:
            if args.bootstrap_admin:
                print(_bootstrap_admin(session, args.email, roles))
                return 0

            if args.revoke_sessions:
                user = users.get_user(session, args.email)
                if user is None:
                    raise SystemExit(f"No user {args.email}.")
                user = user_admin.revoke_sessions(session, actor=None, user=user)
                print(f"Revoked all sessions for {user.email} (token_version={user.token_version}).")
                return 0

            password = _read_password()
            if args.reset_password:
                user = users.get_user(session, args.email)
                if user is None:
                    raise SystemExit(f"No user {args.email}.")
                users.check_password_policy(password)
                user.password_hash = users.hash_password(password)
                user.token_version += 1  # a reset must also end the old sessions
                session.add(user)
                log_event(
                    session, entity_type=user_admin.ENTITY_TYPE, entity_id=user.id, action=ACTION_USER_PASSWORD_RESET,
                    actor=ACTOR_SYSTEM, detail={"email": user.email, "via": VIA}, commit=False,
                )
                session.commit()
                print(f"Password updated for {user.email}.")
            else:
                user = user_admin.create(session, actor=None, email=args.email, password=password, roles=roles, via=VIA)
                print(f"Created {user.email} with roles: {', '.join(roles) or '(none)'}.")
        except users.UserError as exc:
            raise SystemExit(str(exc))
        except user_admin.LastAdminError as exc:
            raise SystemExit(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
