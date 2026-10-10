"""Password login (POST /auth/login) and the proposer != approver rule."""

from __future__ import annotations

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from backend.api.main import app
from backend.api.deps import ROLE_ANALYST
from backend.data.persistence import get_or_create_company
from backend.governance import approval
from backend.models.app_user import AppUser
from backend.models.enums import AssumptionStatus
from backend.services import users
from backend.tests.fixtures.auth import TEST_JWT_SECRET, auth_headers

client = TestClient(app, raise_server_exceptions=False)

PASSWORD = "correct horse battery staple"


@pytest.fixture(autouse=True)
def _fresh_throttle():
    users.reset_throttle()
    yield
    users.reset_throttle()


def _login(email: str, password: str):
    return client.post("/auth/login", json={"email": email, "password": password})


@pytest.fixture()
def analyst(session):
    return users.create_user(session, "Alice@Firm.com", PASSWORD, [ROLE_ANALYST])


# --------------------------------------------------------------------------
# users / hashing
# --------------------------------------------------------------------------


def test_password_is_stored_as_an_argon2id_hash_never_plaintext(session, analyst):
    row = session.exec(select(AppUser)).one()
    assert row.password_hash.startswith("$argon2id$")
    assert PASSWORD not in row.password_hash
    assert users.verify_password(PASSWORD, row.password_hash)
    assert not users.verify_password("wrong", row.password_hash)


def test_same_password_gets_a_different_salted_hash(session):
    a = users.create_user(session, "a@firm.com", PASSWORD)
    b = users.create_user(session, "b@firm.com", PASSWORD)
    assert a.password_hash != b.password_hash


def test_email_is_normalised_and_unique(session, analyst):
    assert analyst.email == "alice@firm.com"
    with pytest.raises(users.UserError):
        users.create_user(session, " ALICE@firm.com ", PASSWORD)


def test_weak_password_and_bad_email_are_rejected(session):
    with pytest.raises(users.UserError):
        users.create_user(session, "x@firm.com", "short")
    with pytest.raises(users.UserError):
        users.create_user(session, "not-an-email", PASSWORD)


def test_garbage_hash_never_verifies():
    assert users.verify_password("anything", "not-a-hash") is False


# --------------------------------------------------------------------------
# POST /auth/login
# --------------------------------------------------------------------------


def test_login_returns_a_short_lived_token_with_subject_and_roles(analyst):
    resp = _login("alice@firm.com", PASSWORD)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["subject"] == "alice@firm.com" and body["roles"] == ["analyst"]
    claims = jwt.decode(body["access_token"], TEST_JWT_SECRET, algorithms=["HS256"])
    assert claims["sub"] == "alice@firm.com" and claims["roles"] == ["analyst"]
    assert claims["exp"] - claims["iat"] == body["expires_in"] == 3600
    assert "password" not in resp.text


def test_login_is_case_insensitive_on_email(analyst):
    assert _login("ALICE@FIRM.COM", PASSWORD).status_code == 200


def test_login_token_works_on_protected_routes_and_role_gate(session, analyst):
    token = _login("alice@firm.com", PASSWORD).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    assert client.get("/valuation/company/1/assumptions", headers=headers).status_code == 200
    # Past the 403 role gate (the decision itself doesn't exist -> 400).
    assert client.post("/valuation/assumptions/999/decide", json={"decision": "APPROVE"}, headers=headers).status_code == 400


def test_user_without_roles_gets_a_token_that_cannot_decide(session):
    users.create_user(session, "viewer@firm.com", PASSWORD)
    token = _login("viewer@firm.com", PASSWORD).json()["access_token"]

    resp = client.post(
        "/valuation/assumptions/1/decide", json={"decision": "APPROVE"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 403


@pytest.mark.parametrize(
    "email, password",
    [("alice@firm.com", "wrong password here"), ("nobody@firm.com", PASSWORD)],
    ids=["wrong-password", "unknown-user"],
)
def test_bad_credentials_get_the_same_generic_401(analyst, email, password):
    resp = _login(email, password)
    assert resp.status_code == 401
    assert resp.json() == {"detail": "Invalid email or password."}
    assert "access_token" not in resp.text


def test_inactive_user_cannot_log_in_and_looks_like_a_bad_password(session, analyst):
    analyst.is_active = False
    session.add(analyst)
    session.commit()

    resp = _login("alice@firm.com", PASSWORD)
    assert resp.status_code == 401 and resp.json() == {"detail": "Invalid email or password."}


def test_repeated_failures_are_throttled_and_even_the_right_password_waits(analyst):
    for _ in range(users.MAX_FAILURES):
        assert _login("alice@firm.com", "wrong password here").status_code == 401

    resp = _login("alice@firm.com", PASSWORD)

    assert resp.status_code == 429
    assert int(resp.headers["retry-after"]) > 0


def test_success_resets_the_failure_counter(analyst):
    for _ in range(users.MAX_FAILURES - 1):
        _login("alice@firm.com", "wrong password here")
    assert _login("alice@firm.com", PASSWORD).status_code == 200
    for _ in range(users.MAX_FAILURES - 1):
        assert _login("alice@firm.com", "wrong password here").status_code == 401


def test_login_records_last_login(session, analyst):
    assert analyst.last_login_at is None
    _login("alice@firm.com", PASSWORD)
    session.refresh(analyst)
    assert analyst.last_login_at is not None


@pytest.mark.parametrize(
    "body",
    [{}, {"email": "a@b.co"}, {"email": "a@b.co", "password": ""}, {"email": "a@b.co", "password": "x" * 2000},
     {"email": "a@b.co", "password": "x", "roles": ["analyst"]}],
)
def test_malformed_login_bodies_are_422(body):
    assert client.post("/auth/login", json=body).status_code == 422


def test_login_fails_closed_without_a_signing_secret(analyst, monkeypatch):
    monkeypatch.delenv("AUTH_JWT_SECRET")
    assert _login("alice@firm.com", PASSWORD).status_code == 503


def test_token_lifetime_is_configurable_and_clamped(analyst, monkeypatch):
    monkeypatch.setenv("AUTH_TOKEN_MINUTES", "5")
    assert _login("alice@firm.com", PASSWORD).json()["expires_in"] == 300
    monkeypatch.setenv("AUTH_TOKEN_MINUTES", "999999")
    assert _login("alice@firm.com", PASSWORD).json()["expires_in"] == 24 * 3600
    monkeypatch.setenv("AUTH_TOKEN_MINUTES", "junk")
    assert _login("alice@firm.com", PASSWORD).json()["expires_in"] == 3600


# --------------------------------------------------------------------------
# proposer != approver
# --------------------------------------------------------------------------


def _propose_via_api(headers, company_id: int) -> dict:
    resp = client.post(
        "/valuation/assumptions/propose",
        json={
            "company_id": company_id,
            "assumption_key": "terminal_growth_rate",
            "ai_recommended_value": 0.03,
            "ai_rationale": "x",
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.fixture()
def company_id(session):
    return get_or_create_company(session, ticker="AAPL", cik10="0000320193", name="Apple Inc.").id


def _decide(decision_id: int, headers, decision: str = "APPROVE", **extra):
    return client.post(f"/valuation/assumptions/{decision_id}/decide", json={"decision": decision, **extra}, headers=headers)


def test_propose_records_the_caller_as_proposer(session, company_id):
    out = _propose_via_api(auth_headers("alice@firm.com"), company_id)
    assert out["proposed_by"] == "alice@firm.com"


@pytest.mark.parametrize("decision, extra", [("APPROVE", {}), ("EDIT", {"human_value": 0.02}), ("REJECT", {})])
def test_proposer_cannot_decide_their_own_proposal(session, company_id, decision, extra):
    alice = auth_headers("alice@firm.com")
    proposal = _propose_via_api(alice, company_id)

    resp = _decide(proposal["id"], alice, decision, **extra)

    assert resp.status_code == 403
    assert "different user" in resp.json()["detail"]
    row = approval.get_decision(session, proposal["id"])
    session.refresh(row)
    assert row.status == AssumptionStatus.PENDING and row.decided_by is None and row.approved_value is None


def test_the_check_ignores_email_case(session, company_id):
    proposal = _propose_via_api(auth_headers("alice@firm.com"), company_id)
    assert _decide(proposal["id"], auth_headers("ALICE@Firm.com")).status_code == 403


def test_a_different_analyst_can_decide(session, company_id):
    proposal = _propose_via_api(auth_headers("alice@firm.com"), company_id)

    resp = _decide(proposal["id"], auth_headers("bob@firm.com"))

    assert resp.status_code == 200, resp.text
    assert resp.json()["decided_by"] == "bob@firm.com" and resp.json()["proposed_by"] == "alice@firm.com"
    assert resp.json()["status"] == AssumptionStatus.APPROVED.value


def test_a_blocked_attempt_does_not_burn_the_proposal(session, company_id):
    alice, bob = auth_headers("alice@firm.com"), auth_headers("bob@firm.com")
    proposal = _propose_via_api(alice, company_id)

    assert _decide(proposal["id"], alice).status_code == 403
    assert _decide(proposal["id"], bob, "EDIT", human_value=0.025).status_code == 200


def test_proposals_without_a_recorded_proposer_are_not_blocked(session, company_id):
    legacy = approval.propose(
        session, company_id=company_id, assumption_key="wacc", ai_recommended_value=0.09, ai_rationale="x"
    )
    assert legacy.proposed_by is None
    assert _decide(legacy.id, auth_headers("alice@firm.com")).status_code == 200


def test_login_flow_end_to_end_enforces_separation(session, company_id):
    users.create_user(session, "alice@firm.com", PASSWORD, [ROLE_ANALYST])
    users.create_user(session, "bob@firm.com", PASSWORD, [ROLE_ANALYST])
    alice = {"Authorization": "Bearer " + _login("alice@firm.com", PASSWORD).json()["access_token"]}
    bob = {"Authorization": "Bearer " + _login("bob@firm.com", PASSWORD).json()["access_token"]}

    proposal = _propose_via_api(alice, company_id)

    assert _decide(proposal["id"], alice).status_code == 403
    assert _decide(proposal["id"], bob).status_code == 200
