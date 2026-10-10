"""Admin-only user administration: /admin/users, the last-admin guard, auditing, and the CLI bootstrap."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from backend import db as db_module
from backend.api import deps
from backend.api.main import app
from backend.audit.logger import (
    ACTION_USER_CREATED,
    ACTION_USER_DEACTIVATED,
    ACTION_USER_PASSWORD_RESET,
    ACTION_USER_REACTIVATED,
    ACTION_USER_ROLES_CHANGED,
    ACTION_USER_SESSIONS_REVOKED,
    get_audit_trail,
)
from backend.models.app_user import AppUser
from backend.models.audit_log_entry import AuditLogEntry
from backend.services import user_admin, users
from backend.tests.fixtures.auth import TEST_JWT_SECRET, auth_headers

client = TestClient(app, raise_server_exceptions=False)
ADMIN = "admin@firm.com"
PASSWORD = "correct horse battery staple"
REPO = Path(__file__).resolve().parents[2]


def admin_headers():
    return auth_headers(ADMIN)


def _uid(session, email: str) -> int:
    return session.exec(select(AppUser).where(AppUser.email == email)).one().id


def _audit(session, user_id: int) -> list[dict]:
    return get_audit_trail(session, entity_type="AppUser", entity_id=user_id)


def _post(body, headers=None):
    return client.post("/admin/users", json=body, headers=headers or admin_headers())


def _patch(user_id: int, body, headers=None):
    return client.patch(f"/admin/users/{user_id}", json=body, headers=headers or admin_headers())


# --------------------------------------------------------------------------
# access control
# --------------------------------------------------------------------------


@pytest.mark.parametrize("who", ["analyst@firm.com", "alice@firm.com", "viewer@firm.com"])
def test_only_admins_can_use_the_admin_endpoints(session, who):
    headers = auth_headers(who)
    uid = _uid(session, "bob@firm.com")
    assert client.get("/admin/users", headers=headers).status_code == 403
    assert _post({"email": "x@example.com", "password": PASSWORD}, headers).status_code == 403
    assert _patch(uid, {"is_active": False}, headers).status_code == 403
    assert client.get(f"/admin/users/{uid}/audit", headers=headers).status_code == 403
    assert session.exec(select(AppUser).where(AppUser.email == "x@example.com")).first() is None
    assert session.get(AppUser, uid).is_active  # nothing changed


def test_the_admin_endpoints_need_a_token():
    assert client.get("/admin/users").status_code == 401
    assert client.post("/admin/users", json={}).status_code == 401
    assert client.patch("/admin/users/1", json={}).status_code == 401


def test_admin_alone_does_not_grant_analyst_powers():
    resp = client.post("/valuation/assumptions/1/decide", json={"decision": "APPROVE"}, headers=admin_headers())
    assert resp.status_code == 403


def test_role_constants_agree_between_deps_and_users():
    assert (deps.ROLE_ADMIN, deps.ROLE_ANALYST) == (users.ROLE_ADMIN, users.ROLE_ANALYST)
    assert users.ALLOWED_ROLES == {"analyst", "admin"}


# --------------------------------------------------------------------------
# GET /admin/users
# --------------------------------------------------------------------------


def test_list_returns_users_without_any_credentials():
    resp = client.get("/admin/users", headers=admin_headers())

    assert resp.status_code == 200
    body = resp.json()
    emails = {u["email"] for u in body["users"]}
    assert {"admin@firm.com", "alice@firm.com", "viewer@firm.com"} <= emails
    assert body["total"] == len(body["users"])
    assert "password" not in resp.text and "$argon2" not in resp.text and "!not-a-real-hash" not in resp.text
    admin_row = next(u for u in body["users"] if u["email"] == ADMIN)
    assert set(admin_row) == {"id", "email", "roles", "is_active", "token_version", "created_at", "last_login_at"}
    assert admin_row["roles"] == ["admin"] and admin_row["is_active"] is True


def test_list_pagination_and_active_filter(session):
    session.add(AppUser(email="zed@firm.com", password_hash="!x", is_active=False))
    session.commit()

    page = client.get("/admin/users?limit=2&offset=1", headers=admin_headers()).json()
    assert len(page["users"]) == 2 and page["total"] > 2
    inactive = client.get("/admin/users?is_active=false", headers=admin_headers()).json()
    assert [u["email"] for u in inactive["users"]] == ["zed@firm.com"] and inactive["total"] == 1
    assert all(u["is_active"] for u in client.get("/admin/users?is_active=true", headers=admin_headers()).json()["users"])
    assert client.get("/admin/users?limit=0", headers=admin_headers()).status_code == 422
    assert client.get("/admin/users?limit=501", headers=admin_headers()).status_code == 422


# --------------------------------------------------------------------------
# POST /admin/users
# --------------------------------------------------------------------------


def test_create_user_returns_201_and_the_user_can_sign_in(session):
    resp = _post({"email": "New.Hire@Example.com", "password": PASSWORD, "roles": ["analyst"]})

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["email"] == "new.hire@example.com" and body["roles"] == ["analyst"] and body["is_active"] is True
    assert body["token_version"] == 0 and body["last_login_at"] is None
    assert PASSWORD not in resp.text
    login = client.post("/auth/login", json={"email": "new.hire@example.com", "password": PASSWORD})
    assert login.status_code == 200 and login.json()["roles"] == ["analyst"]


def test_create_user_audits_the_acting_admin_and_never_the_password(session):
    uid = _post({"email": "audited@example.com", "password": PASSWORD, "roles": ["analyst", "admin"]}).json()["id"]

    [entry] = _audit(session, uid)

    assert entry["action"] == ACTION_USER_CREATED and entry["role"] == "ADMINISTRATION"
    assert entry["actor"] == "human:admin@firm.com"
    assert entry["detail"] == {"email": "audited@example.com", "roles": ["admin", "analyst"]}
    dump = str(session.exec(select(AuditLogEntry)).all())
    assert PASSWORD not in dump and "argon2" not in dump


def test_create_user_with_no_roles_makes_a_read_only_account():
    body = _post({"email": "ro@example.com", "password": PASSWORD}).json()
    assert body["roles"] == []


def test_roles_are_deduplicated_and_sorted():
    assert _post({"email": "d@example.com", "password": PASSWORD, "roles": ["analyst", "admin", "analyst"]}).json()["roles"] == [
        "admin",
        "analyst",
    ]


def test_duplicate_email_is_409_case_insensitively_and_creates_nothing(session):
    resp = _post({"email": "ALICE@firm.com", "password": PASSWORD})
    assert resp.status_code == 409
    assert len(session.exec(select(AppUser).where(AppUser.email == "alice@firm.com")).all()) == 1
    assert not [e for e in session.exec(select(AuditLogEntry)).all() if e.action == ACTION_USER_CREATED]


@pytest.mark.parametrize(
    "body",
    [
        {"email": "a@example.com", "password": "short"},
        {"email": "a@example.com", "password": "x" * 1025},
        {"email": "a@example.com", "password": PASSWORD, "roles": ["superuser"]},
        {"email": "a@example.com", "password": PASSWORD, "roles": ["analist"]},
        {"email": "a@example.com", "password": PASSWORD, "roles": ["ADMIN"]},
        {"email": "a@example.com", "password": PASSWORD, "is_active": False},
        {"email": "a@example.com", "password": PASSWORD, "roles": ["analyst"] * 9},
        {"password": PASSWORD},
        {"email": "ab", "password": PASSWORD},
    ],
)
def test_invalid_create_requests_are_rejected_without_side_effects(session, body):
    before = len(session.exec(select(AppUser)).all())
    assert _post(body).status_code == 422
    assert len(session.exec(select(AppUser)).all()) == before


def test_an_email_without_an_at_sign_is_rejected(session):
    resp = _post({"email": "not-an-email", "password": PASSWORD})
    assert resp.status_code == 422
    assert session.exec(select(AppUser).where(AppUser.email == "not-an-email")).first() is None


# --------------------------------------------------------------------------
# PATCH /admin/users/{id}: roles
# --------------------------------------------------------------------------


def test_changing_roles_takes_effect_immediately_and_is_audited(session):
    uid = _uid(session, "viewer@firm.com")
    headers = auth_headers("viewer@firm.com")
    decide = lambda: client.post("/valuation/assumptions/1/decide", json={"decision": "APPROVE"}, headers=headers).status_code
    assert decide() == 403

    resp = _patch(uid, {"roles": ["analyst"]})

    assert resp.status_code == 200 and resp.json()["roles"] == ["analyst"]
    assert decide() == 400  # past the role gate (the proposal doesn't exist)
    [entry] = _audit(session, uid)
    assert entry["action"] == ACTION_USER_ROLES_CHANGED
    assert entry["actor"] == "human:admin@firm.com"
    assert entry["detail"] == {"email": "viewer@firm.com", "before": [], "after": ["analyst"]}


def test_roles_replace_the_whole_list_and_can_be_emptied(session):
    uid = _uid(session, "alice@firm.com")
    assert _patch(uid, {"roles": []}).json()["roles"] == []
    assert client.post(
        "/valuation/assumptions/1/decide", json={"decision": "APPROVE"}, headers=auth_headers("alice@firm.com")
    ).status_code == 403


def test_a_new_admin_can_use_the_admin_api(session):
    uid = _uid(session, "alice@firm.com")
    alice = auth_headers("alice@firm.com")
    assert client.get("/admin/users", headers=alice).status_code == 403

    assert _patch(uid, {"roles": ["analyst", "admin"]}).status_code == 200

    assert client.get("/admin/users", headers=alice).status_code == 200


def test_setting_the_same_roles_is_a_no_op_with_no_audit_entry(session):
    uid = _uid(session, "alice@firm.com")
    assert _patch(uid, {"roles": ["analyst"]}).status_code == 200
    assert _audit(session, uid) == []


@pytest.mark.parametrize("roles", [["superuser"], ["analyst", "root"], ["Admin"]])
def test_unknown_roles_are_rejected_and_change_nothing(session, roles):
    uid = _uid(session, "alice@firm.com")
    assert _patch(uid, {"roles": roles}).status_code == 422
    assert session.get(AppUser, uid).roles == "analyst" and _audit(session, uid) == []


# --------------------------------------------------------------------------
# PATCH /admin/users/{id}: deactivate / reactivate / revoke
# --------------------------------------------------------------------------


def test_deactivating_ends_the_users_access_and_revokes_their_sessions(session):
    uid = _uid(session, "alice@firm.com")
    alice = auth_headers("alice@firm.com")
    assert client.get("/valuation/company/1/assumptions", headers=alice).status_code == 200

    resp = _patch(uid, {"is_active": False})

    assert resp.status_code == 200 and resp.json()["is_active"] is False and resp.json()["token_version"] == 1
    assert client.get("/valuation/company/1/assumptions", headers=alice).status_code == 401
    assert [e["action"] for e in _audit(session, uid)] == [ACTION_USER_DEACTIVATED, ACTION_USER_SESSIONS_REVOKED]
    revoked = _audit(session, uid)[1]["detail"]
    assert revoked["reason"] == "deactivated" and revoked["token_version"] == 1


def test_reactivating_does_not_resurrect_old_tokens(session):
    uid = _uid(session, "alice@firm.com")
    old = auth_headers("alice@firm.com")
    _patch(uid, {"is_active": False})

    resp = _patch(uid, {"is_active": True})

    assert resp.status_code == 200 and resp.json()["is_active"] is True
    assert client.get("/valuation/company/1/assumptions", headers=old).status_code == 401  # revoked at deactivation
    fresh = {"Authorization": "Bearer " + deps.create_access_token("alice@firm.com", ["analyst"], token_version=1)}
    assert client.get("/valuation/company/1/assumptions", headers=fresh).status_code == 200
    assert _audit(session, uid)[-1]["action"] == ACTION_USER_REACTIVATED


def test_a_deactivated_user_cannot_log_in_until_reactivated(session):
    users.create_user(session, "temp@example.com", PASSWORD, ["analyst"])
    uid = _uid(session, "temp@example.com")
    _patch(uid, {"is_active": False})
    assert client.post("/auth/login", json={"email": "temp@example.com", "password": PASSWORD}).status_code == 401
    _patch(uid, {"is_active": True})
    assert client.post("/auth/login", json={"email": "temp@example.com", "password": PASSWORD}).status_code == 200


def test_revoke_sessions_signs_the_user_out_without_deactivating(session):
    uid = _uid(session, "bob@firm.com")
    bob = auth_headers("bob@firm.com")

    resp = _patch(uid, {"revoke_sessions": True})

    assert resp.status_code == 200 and resp.json()["token_version"] == 1 and resp.json()["is_active"] is True
    assert client.get("/valuation/company/1/assumptions", headers=bob).status_code == 401
    [entry] = _audit(session, uid)
    assert entry["action"] == ACTION_USER_SESSIONS_REVOKED and entry["detail"]["reason"] == "requested"
    assert entry["actor"] == "human:admin@firm.com"


def test_revoke_false_is_not_an_action_but_still_counts_as_a_field(session):
    uid = _uid(session, "bob@firm.com")
    resp = _patch(uid, {"revoke_sessions": False})
    assert resp.status_code == 200 and resp.json()["token_version"] == 0 and _audit(session, uid) == []


def test_one_request_can_change_roles_and_revoke_with_one_audit_entry_each(session):
    uid = _uid(session, "bob@firm.com")
    resp = _patch(uid, {"roles": [], "revoke_sessions": True})
    assert resp.json()["roles"] == [] and resp.json()["token_version"] == 1
    assert [e["action"] for e in _audit(session, uid)] == [ACTION_USER_ROLES_CHANGED, ACTION_USER_SESSIONS_REVOKED]


def test_deactivating_an_already_inactive_user_changes_and_audits_nothing(session):
    uid = _uid(session, "bob@firm.com")
    _patch(uid, {"is_active": False})
    before = len(_audit(session, uid))
    assert _patch(uid, {"is_active": False}).status_code == 200
    assert len(_audit(session, uid)) == before


@pytest.mark.parametrize("body", [{}, {"email": "x@example.com"}, {"password": "x" * 12}, {"is_active": "maybe"}, {"roles": "analyst"}])
def test_invalid_patch_bodies_are_422(session, body):
    uid = _uid(session, "bob@firm.com")
    assert _patch(uid, body).status_code == 422
    assert _audit(session, uid) == []


def test_patching_an_unknown_user_is_404():
    assert _patch(99999, {"is_active": False}).status_code == 404
    assert client.get("/admin/users/99999/audit", headers=admin_headers()).status_code == 404


# --------------------------------------------------------------------------
# the last-admin guard
# --------------------------------------------------------------------------


def test_the_last_admin_cannot_remove_their_own_admin_role(session):
    uid = _uid(session, ADMIN)

    resp = _patch(uid, {"roles": ["analyst"]})

    assert resp.status_code == 409 and "last active admin" in resp.json()["detail"]
    assert session.get(AppUser, uid).roles == "admin" and _audit(session, uid) == []
    assert client.get("/admin/users", headers=admin_headers()).status_code == 200  # still an admin


def test_the_last_admin_cannot_deactivate_themselves(session):
    uid = _uid(session, ADMIN)
    assert _patch(uid, {"is_active": False}).status_code == 409
    assert session.get(AppUser, uid).is_active and _audit(session, uid) == []


def test_a_rejected_combined_request_changes_nothing_at_all(session):
    uid = _uid(session, ADMIN)

    resp = _patch(uid, {"roles": [], "is_active": False, "revoke_sessions": True})

    assert resp.status_code == 409
    row = session.get(AppUser, uid)
    assert (row.roles, row.is_active, row.token_version) == ("admin", True, 0)
    assert _audit(session, uid) == []


def test_a_last_admin_may_still_revoke_their_own_sessions_or_keep_admin_in_a_role_edit(session):
    uid = _uid(session, ADMIN)
    assert _patch(uid, {"roles": ["admin", "analyst"]}).status_code == 200  # stays an admin
    assert _patch(uid, {"revoke_sessions": True}).status_code == 200
    assert client.get("/admin/users", headers=admin_headers()).status_code == 401  # their own token is now revoked


def test_with_a_second_admin_an_admin_may_demote_or_deactivate_themselves(session):
    users.create_user(session, "second@example.com", PASSWORD, ["admin"])
    uid = _uid(session, ADMIN)

    assert _patch(uid, {"roles": ["analyst"]}).status_code == 200  # self-demotion is fine now
    assert client.get("/admin/users", headers=admin_headers()).status_code == 403  # and takes effect at once


def test_demoting_the_other_admin_is_fine_but_then_the_remaining_one_is_protected(session):
    users.create_user(session, "second@example.com", PASSWORD, ["admin"])
    second = {"Authorization": "Bearer " + deps.create_access_token("second@example.com", ["admin"])}
    admin_id, second_id = _uid(session, ADMIN), _uid(session, "second@example.com")

    assert _patch(admin_id, {"is_active": False}, second).status_code == 200  # second removes the original admin
    assert _patch(second_id, {"roles": []}, second).status_code == 409  # second is now the last one
    assert _patch(second_id, {"is_active": False}, second).status_code == 409


def test_an_inactive_admin_does_not_count_as_a_spare(session):
    users.create_user(session, "ghost@example.com", PASSWORD, ["admin"])
    session.exec(select(AppUser).where(AppUser.email == "ghost@example.com")).one().is_active = False
    session.commit()
    assert _patch(_uid(session, ADMIN), {"roles": []}).status_code == 409  # the deactivated admin is no help


def test_non_admins_can_always_be_changed(session):
    for email in ("alice@firm.com", "bob@firm.com"):
        assert _patch(_uid(session, email), {"is_active": False}).status_code == 200


# --------------------------------------------------------------------------
# audit trail endpoint, and /auth/revoke-sessions is audited too
# --------------------------------------------------------------------------


def test_the_audit_endpoint_returns_the_account_history_in_order(session):
    uid = _post({"email": "hist@example.com", "password": PASSWORD, "roles": ["analyst"]}).json()["id"]
    _patch(uid, {"roles": ["analyst", "admin"]})
    _patch(uid, {"is_active": False})

    trail = client.get(f"/admin/users/{uid}/audit", headers=admin_headers()).json()

    assert [e["action"] for e in trail] == [
        ACTION_USER_CREATED,
        ACTION_USER_ROLES_CHANGED,
        ACTION_USER_DEACTIVATED,
        ACTION_USER_SESSIONS_REVOKED,
    ]
    assert {e["actor"] for e in trail} == {"human:admin@firm.com"}


def test_auth_revoke_sessions_is_now_audited_with_the_admin_as_actor(session):
    uid = _uid(session, "bob@firm.com")
    resp = client.post("/auth/revoke-sessions", json={"email": "bob@firm.com"}, headers=admin_headers())
    assert resp.status_code == 200 and resp.json()["token_version"] == 1
    [entry] = _audit(session, uid)
    assert entry["action"] == ACTION_USER_SESSIONS_REVOKED and entry["actor"] == "human:admin@firm.com"


def test_a_change_and_its_audit_entry_roll_back_together(session, monkeypatch):
    from backend.services import user_admin as ua

    uid = _uid(session, "alice@firm.com")

    def boom(*a, **k):
        raise RuntimeError("audit store unavailable")

    monkeypatch.setattr(ua, "log_event", boom)
    with pytest.raises(RuntimeError):
        ua.update(session, actor=ADMIN, user_id=uid, roles=[])

    session.expire_all()
    assert session.get(AppUser, uid).roles == "analyst"  # the change did not survive its failed audit write


def test_service_level_validation(session):
    with pytest.raises(users.UserError):
        users.validate_roles(["analyst", "nope"])
    assert users.validate_roles([" analyst ", "", "admin", "analyst"]) == ["admin", "analyst"]
    with pytest.raises(user_admin.UserNotFoundError):
        user_admin.update(session, actor=ADMIN, user_id=12345, roles=[])


# --------------------------------------------------------------------------
# scripts/create_user.py
# --------------------------------------------------------------------------


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    """An EMPTY database (no seeded roster, so no pre-existing admin) shared with subprocesses."""
    url = f"sqlite:///{tmp_path / 'cli.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    db_module.reset_engine()
    db_module.create_all()
    yield url
    db_module.reset_engine()


def _cli(*args, password=PASSWORD):
    env = {**__import__("os").environ, "AUTH_JWT_SECRET": TEST_JWT_SECRET, "SOTP_NEW_PASSWORD": password}
    return subprocess.run(
        [sys.executable, "-I", str(REPO / "scripts" / "create_user.py"), *args],
        env=env, capture_output=True, text=True,
    )


def test_cli_bootstraps_the_first_admin_and_audits_it_as_system(fresh_db):
    out = _cli("--email", "root@example.com", "--bootstrap-admin")

    assert out.returncode == 0, out.stderr
    assert "Created first admin root@example.com" in out.stdout
    with Session(db_module.get_engine()) as s:
        user = s.exec(select(AppUser)).one()
        assert user.email == "root@example.com" and users.parse_roles(user.roles) == ["admin", "analyst"]
        [entry] = _audit(s, user.id)
        assert entry["action"] == ACTION_USER_CREATED and entry["actor"] == "system"
        assert entry["detail"]["via"] == "scripts/create_user.py"
    # ...and that admin can log in and use the API.
    login = TestClient(app).post("/auth/login", json={"email": "root@example.com", "password": PASSWORD})
    assert login.status_code == 200 and "admin" in login.json()["roles"]


def test_cli_bootstrap_refuses_once_an_active_admin_exists(fresh_db):
    assert _cli("--email", "root@example.com", "--bootstrap-admin").returncode == 0

    out = _cli("--email", "other@example.com", "--bootstrap-admin")

    assert out.returncode != 0 and "active admin already exists" in out.stderr
    with Session(db_module.get_engine()) as s:
        assert [u.email for u in s.exec(select(AppUser)).all()] == ["root@example.com"]


def test_cli_bootstrap_can_promote_an_existing_user_without_a_password(fresh_db):
    assert _cli("--email", "ana@example.com", "--roles", "analyst").returncode == 0

    out = _cli("--email", "ana@example.com", "--bootstrap-admin", password="")

    assert out.returncode == 0, out.stderr
    assert "Promoted existing user ana@example.com" in out.stdout
    with Session(db_module.get_engine()) as s:
        user = s.exec(select(AppUser)).one()
        assert users.parse_roles(user.roles) == ["admin", "analyst"] and user.is_active
        assert [e["action"] for e in _audit(s, user.id)] == [ACTION_USER_CREATED, ACTION_USER_ROLES_CHANGED]


def test_cli_bootstrap_ignores_deactivated_admins(fresh_db):
    assert _cli("--email", "old@example.com", "--bootstrap-admin").returncode == 0
    with Session(db_module.get_engine()) as s:
        u = s.exec(select(AppUser)).one()
        u.is_active = False
        s.add(u)
        s.commit()

    assert _cli("--email", "new@example.com", "--bootstrap-admin").returncode == 0  # nobody active is left


def test_cli_rejects_unknown_roles_and_weak_passwords(fresh_db):
    bad_role = _cli("--email", "a@example.com", "--roles", "superuser")
    assert bad_role.returncode != 0 and "Unknown role" in bad_role.stderr
    weak = _cli("--email", "a@example.com", password="short")
    assert weak.returncode != 0 and "at least 12" in weak.stderr


def test_cli_reset_and_revoke_are_audited_as_system(fresh_db):
    assert _cli("--email", "u@example.com").returncode == 0
    assert _cli("--email", "u@example.com", "--reset-password", password=PASSWORD + "-2").returncode == 0
    assert _cli("--email", "u@example.com", "--revoke-sessions").returncode == 0

    with Session(db_module.get_engine()) as s:
        user = s.exec(select(AppUser)).one()
        trail = _audit(s, user.id)
        assert [e["action"] for e in trail] == [ACTION_USER_CREATED, ACTION_USER_PASSWORD_RESET, ACTION_USER_SESSIONS_REVOKED]
        assert {e["actor"] for e in trail} == {"system"}
        assert user.token_version == 2  # the reset bumped it, then the explicit revoke
        assert PASSWORD not in str(trail)
