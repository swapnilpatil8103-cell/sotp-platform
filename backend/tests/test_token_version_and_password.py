"""token_version, POST /auth/change-password and the admin "revoke sessions" action."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from backend.api.deps import create_access_token
from backend.api.main import app
from backend.models.app_user import AppUser
from backend.services import login_throttle as lt
from backend.services import users
from backend.tests.fixtures.auth import TEST_JWT_SECRET, auth_headers

client = TestClient(app, raise_server_exceptions=False)
PASSWORD = "correct horse battery staple"
NEW_PASSWORD = "a different long passphrase 42"
READ_URL = "/valuation/company/1/assumptions"


def _login(email: str, password: str):
    return client.post("/auth/login", json={"email": email, "password": password})


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _change(headers, current: str = PASSWORD, new: str = NEW_PASSWORD, **extra):
    return client.post(
        "/auth/change-password", json={"current_password": current, "new_password": new, **extra}, headers=headers
    )


@pytest.fixture()
def carol(session):
    return users.create_user(session, "carol@example.com", PASSWORD, ["analyst"])


def _token(email: str = "carol@example.com", password: str = PASSWORD) -> str:
    resp = _login(email, password)
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


# --------------------------------------------------------------------------
# tokens carry and are checked against token_version
# --------------------------------------------------------------------------


def test_login_token_embeds_the_users_current_token_version(session, carol):
    carol.token_version = 7
    session.add(carol)
    session.commit()

    claims = jwt.decode(_token(), TEST_JWT_SECRET, algorithms=["HS256"])

    assert claims["tv"] == 7


def test_a_token_stops_working_when_the_version_moves_on(session, carol):
    headers = _bearer(_token())
    assert client.get(READ_URL, headers=headers).status_code == 200

    carol.token_version += 1
    session.add(carol)
    session.commit()

    resp = client.get(READ_URL, headers=headers)
    assert resp.status_code == 401
    assert "revoked" in resp.json()["detail"].lower()
    assert resp.headers["www-authenticate"] == "Bearer"


def test_a_token_from_the_future_is_rejected_too(carol):
    ahead = create_access_token("carol@example.com", ["analyst"], token_version=carol.token_version + 1)
    assert client.get(READ_URL, headers=_bearer(ahead)).status_code == 401


def test_new_default_version_is_zero(session):
    user = users.create_user(session, "newbie@example.com", PASSWORD)
    assert user.token_version == 0


# --------------------------------------------------------------------------
# POST /auth/change-password
# --------------------------------------------------------------------------


def test_change_password_succeeds_and_returns_a_working_new_token(session, carol):
    old = _bearer(_token())

    resp = _change(old)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["subject"] == "carol@example.com" and body["roles"] == ["analyst"]
    assert jwt.decode(body["access_token"], TEST_JWT_SECRET, algorithms=["HS256"])["tv"] == 1
    assert client.get(READ_URL, headers=_bearer(body["access_token"])).status_code == 200  # stays signed in
    session.refresh(carol)
    assert carol.token_version == 1
    assert carol.password_hash.startswith("$argon2id$") and NEW_PASSWORD not in carol.password_hash


def test_after_a_change_the_old_token_and_old_password_are_dead(carol):
    old = _bearer(_token())
    assert _change(old).status_code == 200

    assert client.get(READ_URL, headers=old).status_code == 401  # the token used to make the change, too
    assert _login("carol@example.com", PASSWORD).status_code == 401
    assert _login("carol@example.com", NEW_PASSWORD).status_code == 200


def test_changing_a_password_signs_out_every_other_session(carol):
    laptop, phone = _bearer(_token()), _bearer(_token())
    assert client.get(READ_URL, headers=phone).status_code == 200

    assert _change(laptop).status_code == 200

    assert client.get(READ_URL, headers=phone).status_code == 401


def test_wrong_current_password_is_403_not_401_and_changes_nothing(session, carol):
    headers = _bearer(_token())

    resp = _change(headers, current="not my password at all")

    assert resp.status_code == 403  # a 401 would make the web app think the session expired
    assert resp.json()["detail"] == "Current password is incorrect."
    session.refresh(carol)
    assert carol.token_version == 0
    assert client.get(READ_URL, headers=headers).status_code == 200  # still signed in
    assert _login("carol@example.com", PASSWORD).status_code == 200


def test_new_password_must_be_at_least_12_characters(carol):
    headers = _bearer(_token())
    resp = _change(headers, new="elevenchars")  # 11
    assert resp.status_code == 422
    assert _change(headers, new="twelve chars").status_code == 200  # exactly 12


def test_new_password_must_differ_from_the_current_one(session, carol):
    headers = _bearer(_token())

    resp = _change(headers, new=PASSWORD)

    assert resp.status_code == 400
    assert "different" in resp.json()["detail"]
    session.refresh(carol)
    assert carol.token_version == 0  # nothing was revoked by a rejected change


def test_overlong_password_and_unknown_fields_are_rejected(carol):
    headers = _bearer(_token())
    assert _change(headers, new="x" * 2000).status_code == 422
    assert _change(headers, user_id="someone-else").status_code == 422


def test_change_password_needs_a_token():
    resp = client.post("/auth/change-password", json={"current_password": "x", "new_password": "y" * 12})
    assert resp.status_code == 401


def test_you_can_only_change_your_own_password(session, carol):
    users.create_user(session, "dave@example.com", PASSWORD, ["analyst"])
    carols_token = _bearer(_token())

    # There is no way to name another user; the endpoint acts on the token's subject.
    resp = client.post(
        "/auth/change-password",
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD, "email": "dave@example.com"},
        headers=carols_token,
    )
    assert resp.status_code == 422
    assert _login("dave@example.com", PASSWORD).status_code == 200


def test_wrong_current_passwords_count_toward_the_login_throttle(carol):
    """A stolen token must not be a free oracle for guessing the password."""
    headers = _bearer(_token())
    for _ in range(lt.PAIR.threshold):
        assert _change(headers, current="not my password at all").status_code == 403

    resp = _change(headers, current=PASSWORD)  # even the right one, now

    assert resp.status_code == 429 and int(resp.headers["retry-after"]) > 0


def test_a_deactivated_user_cannot_change_a_password(session, carol):
    headers = _bearer(_token())
    carol.is_active = False
    session.add(carol)
    session.commit()
    assert _change(headers).status_code == 401


def test_change_password_fails_closed_without_a_signing_secret(carol, monkeypatch):
    headers = _bearer(_token())
    monkeypatch.delenv("AUTH_JWT_SECRET")
    assert _change(headers).status_code == 503


# --------------------------------------------------------------------------
# admin: POST /auth/revoke-sessions
# --------------------------------------------------------------------------


def test_admin_revoke_invalidates_the_target_users_existing_tokens(session):
    alice = auth_headers("alice@firm.com")
    assert client.get(READ_URL, headers=alice).status_code == 200

    resp = client.post("/auth/revoke-sessions", json={"email": "alice@firm.com"}, headers=auth_headers("admin@firm.com"))

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"email": "alice@firm.com", "token_version": 1}
    assert client.get(READ_URL, headers=alice).status_code == 401
    # A token minted for the new version works: the user is not locked out, only signed out.
    fresh = {"Authorization": "Bearer " + create_access_token("alice@firm.com", ["analyst"], token_version=1)}
    assert client.get(READ_URL, headers=fresh).status_code == 200


def test_revoke_leaves_other_users_alone():
    client.post("/auth/revoke-sessions", json={"email": "alice@firm.com"}, headers=auth_headers("admin@firm.com"))
    assert client.get(READ_URL, headers=auth_headers("bob@firm.com")).status_code == 200


def test_revoke_requires_the_admin_role():
    for who in ("analyst@firm.com", "viewer@firm.com"):
        resp = client.post("/auth/revoke-sessions", json={"email": "alice@firm.com"}, headers=auth_headers(who))
        assert resp.status_code == 403
        assert "admin" in resp.json()["detail"]
    assert client.get(READ_URL, headers=auth_headers("alice@firm.com")).status_code == 200  # nothing happened


def test_revoke_needs_a_token_and_a_real_user():
    assert client.post("/auth/revoke-sessions", json={"email": "alice@firm.com"}).status_code == 401
    resp = client.post("/auth/revoke-sessions", json={"email": "nobody@firm.com"}, headers=auth_headers("admin@firm.com"))
    assert resp.status_code == 404


def test_an_admin_can_revoke_themselves(session):
    admin = auth_headers("admin@firm.com")
    assert client.post("/auth/revoke-sessions", json={"email": "ADMIN@firm.com"}, headers=admin).status_code == 200
    assert client.get(READ_URL, headers=admin).status_code == 401


def test_admin_role_alone_does_not_grant_analyst_powers():
    resp = client.post("/valuation/assumptions/1/decide", json={"decision": "APPROVE"}, headers=auth_headers("admin@firm.com"))
    assert resp.status_code == 403


def test_a_revoked_users_login_still_works_and_gets_the_new_version(session, carol):
    old = _bearer(_token())
    client.post("/auth/revoke-sessions", json={"email": "carol@example.com"}, headers=auth_headers("admin@firm.com"))

    assert client.get(READ_URL, headers=old).status_code == 401
    new = _bearer(_token())
    assert client.get(READ_URL, headers=new).status_code == 200
    session.refresh(carol)
    assert carol.token_version == 1


# --------------------------------------------------------------------------
# scripts/create_user.py --revoke-sessions
# --------------------------------------------------------------------------


def test_cli_revoke_sessions_bumps_the_version(session, carol, tmp_path, monkeypatch):
    # The per-test database is a SQLite file; the script reads DATABASE_URL like the app does.
    env = {**__import__("os").environ, "AUTH_JWT_SECRET": TEST_JWT_SECRET}
    headers = _bearer(_token())

    repo = Path(__file__).resolve().parents[2]
    out = subprocess.run(
        [sys.executable, "-I", str(repo / "scripts" / "create_user.py"), "--email", "carol@example.com", "--revoke-sessions"],
        env={**env, "PYTHONPATH": str(repo)},
        capture_output=True,
        text=True,
    )

    assert out.returncode == 0, out.stderr
    assert "token_version=1" in out.stdout
    assert client.get(READ_URL, headers=headers).status_code == 401
    unknown = subprocess.run(
        [sys.executable, "-I", str(repo / "scripts" / "create_user.py"), "--email", "ghost@example.com", "--revoke-sessions"],
        env={**env, "PYTHONPATH": str(repo)},
        capture_output=True,
        text=True,
    )
    assert unknown.returncode != 0 and "No user" in unknown.stderr


def test_mint_dev_token_embeds_the_current_version(session, carol, monkeypatch):
    carol.token_version = 3
    session.add(carol)
    session.commit()
    repo = Path(__file__).resolve().parents[2]
    out = subprocess.run(
        [sys.executable, "-I", str(repo / "scripts" / "mint_dev_token.py"), "--sub", "carol@example.com"],
        env={**__import__("os").environ, "AUTH_JWT_SECRET": TEST_JWT_SECRET, "PYTHONPATH": str(repo)},
        capture_output=True,
        text=True,
    )
    assert out.returncode == 0, out.stderr
    token = out.stdout.strip()
    assert jwt.decode(token, TEST_JWT_SECRET, algorithms=["HS256"])["tv"] == 3
    assert client.get(READ_URL, headers=_bearer(token)).status_code == 200


# --------------------------------------------------------------------------
# the Alembic migration f6a7b8c9d0e1
# --------------------------------------------------------------------------


def test_migration_adds_token_version_defaulting_to_zero_and_the_attempt_table(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect, text

    url = f"sqlite:///{tmp_path / 'mig.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    cfg = Config("backend/alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    engine = create_engine(url)

    command.upgrade(cfg, "e5f6a7b8c9d0")
    with engine.begin() as conn:  # a user created before the migration
        conn.execute(
            text(
                "INSERT INTO app_user (email, password_hash, roles, is_active, created_at) "
                "VALUES ('old@example.com', 'x', 'analyst', 1, '2026-01-01 00:00:00')"
            )
        )
    assert "login_attempt" not in inspect(engine).get_table_names()

    command.upgrade(cfg, "head")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT token_version FROM app_user")).scalar_one() == 0
    cols = {c["name"] for c in inspect(engine).get_columns("login_attempt")}
    assert cols == {"id", "email_hash", "ip", "ts", "success"}
    indexed = {c for ix in inspect(engine).get_indexes("login_attempt") for c in ix["column_names"]}
    assert {"email_hash", "ip", "ts"} <= indexed  # the throttle queries filter on these

    command.downgrade(cfg, "e5f6a7b8c9d0")
    assert "login_attempt" not in inspect(engine).get_table_names()
    assert "token_version" not in {c["name"] for c in inspect(engine).get_columns("app_user")}
