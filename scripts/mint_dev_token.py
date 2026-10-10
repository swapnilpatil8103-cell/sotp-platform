"""Mint a Bearer token for local development.

Real users sign in with ``POST /auth/login`` (create one with
``scripts/create_user.py``). This mints a token directly, without a user row, for
scripts and curl; never put one in the web app. It signs with
``AUTH_JWT_SECRET`` from the environment or the repo's ``.env``.

    python scripts/mint_dev_token.py --sub you@example.com
    python scripts/mint_dev_token.py --sub viewer@example.com --roles ""   # no roles
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

from backend.api.deps import AuthConfigError, create_access_token  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sub", required=True, help="Token subject: recorded as the approver / run author in the audit trail.")
    parser.add_argument("--roles", default="analyst", help='Comma-separated roles (default "analyst"; pass "" for none).')
    parser.add_argument("--hours", type=float, default=8.0, help="Token lifetime in hours (default 8).")
    args = parser.parse_args()

    roles = [r.strip() for r in args.roles.split(",") if r.strip()]
    try:
        token = create_access_token(args.sub, roles, timedelta(hours=args.hours))
    except AuthConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
