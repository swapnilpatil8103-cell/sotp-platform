"""Authentication (401), role authorization (403) and token-derived identity.

Covers: every route except /health and /auth/login rejects an unauthenticated
request; bad tokens are rejected; the "analyst" role gates the decide and run
endpoints; the approver / run author / audit-log actor come from the token
subject, never from the request body; and the user row is loaded on every
request, so deactivating or deleting a user, or changing their roles, takes
effect immediately even though their token is still validly signed.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi.testclient import TestClient

from backend.api.deps import MAX_SUBJECT_LENGTH, AuthConfigError, create_access_token
from backend.api.main import app, origins
from backend.audit.logger import (
    ACTION_DECISION_RECORDED,
    ACTION_VALUATION_RUN_EXECUTED,
    actor_human,
    get_audit_trail,
)
from backend.data.persistence import get_or_create_company
from backend.governance import approval
from backend.models.enums import AssumptionStatus
from backend.tests.fixtures.auth import TEST_JWT_SECRET, auth_headers
from backend.tests.fixtures.run_inputs import dcf_inputs

anon = TestClient(app)  # sends no credentials

# An existing read endpoint that touches no external service (empty DB -> []).
READ_URL = "/valuation/company/1/assumptions"
DECIDE_URL = "/valuation/assumptions/1/decide"


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _forge(claims: dict, *, secret: str = TEST_JWT_SECRET, algorithm: str = "HS256") -> dict[str, str]:
    """A token built outside create_access_token, so malformed claims are possible."""
    return _bearer(jwt.encode(claims, secret, algorithm=algorithm))


def _valid_claims(**overrides) -> dict:
    claims = {
        "sub": "analyst@firm.com",
        "roles": ["analyst"],
        "tv": 0,
        "exp": datetime.now(timezone.utc) + timedelta(hours=1),
    }
    claims.update(overrides)
    return {k: v for k, v in claims.items() if v is not None}


# --------------------------------------------------------------------------
# /health is open; everything else is protected by default
# --------------------------------------------------------------------------


def test_health_is_open_without_a_token():
    assert anon.get("/health").status_code == 200


# The complete list of routes that may be called without a token. Adding a
# route here is a deliberate security decision.
PUBLIC_PATHS = {"/health", "/auth/login"}


def _protected_routes() -> list[tuple[str, str]]:
    # Enumerated from the OpenAPI schema -- the server's real, flattened view of
    # its operations. (Newer FastAPI keeps included routers as lazy objects in
    # app.routes, so walking app.routes would see none of them.)
    spec = app.openapi()
    return sorted(
        (method.upper(), path)
        for path, item in spec["paths"].items()
        if path not in PUBLIC_PATHS
        for method in item
        if method in {"get", "post", "put", "patch", "delete"}
    )


PROTECTED = _protected_routes()


def test_only_health_and_login_are_public():
    all_paths = set(app.openapi()["paths"])
    assert PUBLIC_PATHS <= all_paths
    assert {p for _, p in PROTECTED}.isdisjoint(PUBLIC_PATHS)
    assert all_paths - PUBLIC_PATHS == {p for _, p in PROTECTED}


def test_route_enumeration_is_not_vacuous():
    assert len(PROTECTED) > 20


@pytest.mark.parametrize("method, path", PROTECTED, ids=[f"{m} {p}" for m, p in PROTECTED])
def test_every_route_except_health_requires_a_token(method, path):
    """Secure by default: a new route added to any router is covered, and this
    fails if a router is ever mounted without the auth dependency."""
    url = re.sub(r"\{[^}]+\}", "1", path)

    resp = anon.request(method, url)

    assert resp.status_code == 401, f"{method} {path} -> {resp.status_code}"
    assert resp.headers["www-authenticate"] == "Bearer"


# --------------------------------------------------------------------------
# Token validation
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "headers, label",
    [
        ({}, "no header"),
        ({"Authorization": "Basic YWxpY2U6cHc="}, "wrong scheme"),
        ({"Authorization": "Bearer not.a.jwt"}, "garbage token"),
        (_forge(_valid_claims(), secret="a-completely-different-secret-0123456789abcdef"), "wrong signature"),
        (_forge(_valid_claims(exp=datetime.now(timezone.utc) - timedelta(seconds=30))), "expired"),
        (_forge(_valid_claims(exp=None)), "no exp claim"),
        (_forge(_valid_claims(sub=None)), "no sub claim"),
        (_forge(_valid_claims(sub="   ")), "blank sub"),
        (_forge(_valid_claims(tv=None)), "no tv (token version) claim"),
        (_forge(_valid_claims(tv="0")), "tv is a string"),
        (_forge(_valid_claims(tv=True)), "tv is a bool"),
        (_forge(_valid_claims(tv=1)), "tv does not match the user row"),
        (_forge(_valid_claims(sub="x" * (MAX_SUBJECT_LENGTH + 1))), "sub longer than the DB columns"),
        (_bearer(jwt.encode(_valid_claims(), None, algorithm="none")), "unsigned token (alg=none)"),
    ],
    ids=lambda v: v if isinstance(v, str) else None,
)
def test_invalid_tokens_are_rejected_with_401(headers, label):
    resp = anon.get(READ_URL, headers=headers)
    assert resp.status_code == 401, label


def test_a_valid_token_for_an_unknown_user_is_rejected():
    resp = anon.get(READ_URL, headers=_forge(_valid_claims(sub="ghost@firm.com")))
    assert resp.status_code == 401
    assert "no longer active" in resp.json()["detail"]


def test_expired_token_says_so():
    resp = anon.get(READ_URL, headers=_forge(_valid_claims(exp=datetime.now(timezone.utc) - timedelta(seconds=30))))
    assert resp.status_code == 401
    assert "expired" in resp.json()["detail"].lower()


def test_any_valid_token_can_read_without_the_analyst_role():
    resp = anon.get(READ_URL, headers=auth_headers("viewer@firm.com", roles=()))
    assert resp.status_code == 200


# --------------------------------------------------------------------------
# Role check: only analysts decide and run
# --------------------------------------------------------------------------

DECIDE_BODY = {"decision": "APPROVE"}
RUN_BODY = {"company_id": 1, "method": "DCF", "decision_ids": [1], "inputs": {}}

NON_ANALYST = [
    pytest.param(auth_headers("viewer@firm.com", roles=()), id="no-roles"),
    pytest.param(auth_headers("viewer@firm.com", roles=("viewer",)), id="other-role"),
    pytest.param(_forge(_valid_claims(sub="viewer@firm.com", roles=None)), id="no-roles-claim"),
    # The token CLAIMS analyst, but the user row (the authority) has no roles.
    pytest.param(_forge(_valid_claims(sub="viewer@firm.com", roles=["analyst"])), id="token-claims-analyst-db-says-no"),
]


@pytest.mark.parametrize("headers", NON_ANALYST)
def test_decide_requires_the_analyst_role(headers):
    resp = anon.post(DECIDE_URL, json=DECIDE_BODY, headers=headers)
    assert resp.status_code == 403
    assert "analyst" in resp.json()["detail"]


@pytest.mark.parametrize("headers", NON_ANALYST)
def test_run_requires_the_analyst_role(headers):
    resp = anon.post("/valuation/run", json=RUN_BODY, headers=headers)
    assert resp.status_code == 403
    assert "analyst" in resp.json()["detail"]


def test_a_forbidden_decide_changes_nothing(session):
    company = get_or_create_company(session, ticker="AAPL", cik10="0000320193", name="Apple Inc.")
    pending = approval.propose(
        session, proposed_by="proposer@firm.com", company_id=company.id, assumption_key="wacc", ai_recommended_value=0.09, ai_rationale="x"
    )

    resp = anon.post(
        f"/valuation/assumptions/{pending.id}/decide", json=DECIDE_BODY, headers=auth_headers("v@firm.com", roles=())
    )

    assert resp.status_code == 403
    session.refresh(pending)
    assert pending.status == AssumptionStatus.PENDING and pending.decided_by is None


def test_an_analyst_gets_past_the_role_check():
    # The decision id does not exist, so the handler answers 400 -- the point
    # is that it is not 401/403.
    resp = anon.post(DECIDE_URL, json=DECIDE_BODY, headers=auth_headers("analyst@firm.com"))
    assert resp.status_code == 400


# --------------------------------------------------------------------------
# Identity comes from the token, never the body
# --------------------------------------------------------------------------


def _pending(session, key="wacc", value=0.09):
    company = get_or_create_company(session, ticker="AAPL", cik10="0000320193", name="Apple Inc.")
    return company, approval.propose(
        session, proposed_by="proposer@firm.com", company_id=company.id, assumption_key=key, ai_recommended_value=value, ai_rationale="x"
    )


def test_decided_by_and_audit_actor_are_the_token_subject(session):
    company, pending = _pending(session)

    resp = anon.post(
        f"/valuation/assumptions/{pending.id}/decide",
        json={"decision": "APPROVE"},
        headers=auth_headers("alice@firm.com"),
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["decided_by"] == "alice@firm.com"
    trail = get_audit_trail(session, company_id=company.id)
    decision_entry = next(e for e in trail if e["action"] == ACTION_DECISION_RECORDED)
    assert decision_entry["actor"] == actor_human("alice@firm.com")


def test_cannot_approve_as_someone_else_by_naming_them_in_the_body(session):
    company, pending = _pending(session)

    resp = anon.post(
        f"/valuation/assumptions/{pending.id}/decide",
        json={"decision": "APPROVE", "user_id": "cfo@firm.com"},
        headers=auth_headers("alice@firm.com"),
    )

    assert resp.status_code == 422
    session.refresh(pending)
    assert pending.status == AssumptionStatus.PENDING and pending.decided_by is None
    assert ACTION_DECISION_RECORDED not in [e["action"] for e in get_audit_trail(session, company_id=company.id)]


def test_run_created_by_and_audit_actor_are_the_token_subject(session):
    company, pending = _pending(session)
    approval.record_decision(session, decision_id=pending.id, decision="APPROVE", user_id="alice@firm.com")

    resp = anon.post(
        "/valuation/run",
        json={
            "company_id": company.id,
            "method": "DCF",
            "decision_ids": [pending.id],
            "inputs": dcf_inputs(wacc=0.09),
        },
        headers=auth_headers("bob@firm.com"),
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["created_by"] == "bob@firm.com"
    trail = get_audit_trail(session, company_id=company.id)
    run_entry = next(e for e in trail if e["action"] == ACTION_VALUATION_RUN_EXECUTED)
    assert run_entry["actor"] == actor_human("bob@firm.com")


def test_cannot_author_a_run_as_someone_else_by_naming_them_in_the_body(session):
    company, pending = _pending(session)
    approval.record_decision(session, decision_id=pending.id, decision="APPROVE", user_id="alice@firm.com")

    resp = anon.post(
        "/valuation/run",
        json={
            "company_id": company.id,
            "method": "DCF",
            "decision_ids": [pending.id],
            "inputs": dcf_inputs(wacc=0.09),
            "created_by": "cfo@firm.com",
        },
        headers=auth_headers("bob@firm.com"),
    )

    assert resp.status_code == 422
    assert ACTION_VALUATION_RUN_EXECUTED not in [e["action"] for e in get_audit_trail(session, company_id=company.id)]


def test_audit_trail_attributes_each_step_to_its_own_token(session):
    """Alice approves, Bob runs: two different principals, each recorded as themselves."""
    company, pending = _pending(session)

    anon.post(f"/valuation/assumptions/{pending.id}/decide", json={"decision": "APPROVE"}, headers=auth_headers("alice@firm.com"))
    anon.post(
        "/valuation/run",
        json={"company_id": company.id, "method": "DCF", "decision_ids": [pending.id], "inputs": dcf_inputs(wacc=0.09)},
        headers=auth_headers("bob@firm.com"),
    )

    actors = {e["action"]: e["actor"] for e in get_audit_trail(session, company_id=company.id)}
    assert actors[ACTION_DECISION_RECORDED] == "human:alice@firm.com"
    assert actors[ACTION_VALUATION_RUN_EXECUTED] == "human:bob@firm.com"


# --------------------------------------------------------------------------
# Server configuration fails closed
# --------------------------------------------------------------------------


def test_missing_secret_fails_closed_with_503(monkeypatch):
    headers = auth_headers()  # minted while the secret is still set
    monkeypatch.delenv("AUTH_JWT_SECRET")

    assert anon.get(READ_URL, headers=headers).status_code == 503
    assert anon.get(READ_URL).status_code == 401  # still never lets an anonymous request through


def test_too_short_secret_is_not_accepted(monkeypatch):
    headers = auth_headers()
    monkeypatch.setenv("AUTH_JWT_SECRET", "short")

    assert anon.get(READ_URL, headers=headers).status_code == 503
    with pytest.raises(AuthConfigError):
        create_access_token("alice@firm.com", ["analyst"])


def test_the_503_does_not_leak_secret_details(monkeypatch):
    headers = auth_headers()
    monkeypatch.delenv("AUTH_JWT_SECRET")
    detail = anon.get(READ_URL, headers=headers).json()["detail"]
    assert "AUTH_JWT_SECRET" not in detail and "32" not in detail


# --------------------------------------------------------------------------
# Browsers and tokens
# --------------------------------------------------------------------------


def test_cors_preflight_is_not_blocked_by_auth():
    """Browsers send the OPTIONS preflight without an Authorization header; it
    must succeed and must advertise that Authorization is allowed."""
    resp = anon.options(
        "/valuation/run",
        headers={
            "Origin": origins[0],
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert resp.status_code == 200
    assert "authorization" in resp.headers["access-control-allow-headers"].lower()


def test_token_round_trip_carries_subject_roles_and_expiry():
    token = create_access_token("alice@firm.com", ["analyst", "analyst", "auditor"], timedelta(minutes=5))
    claims = jwt.decode(token, TEST_JWT_SECRET, algorithms=["HS256"])
    assert claims["sub"] == "alice@firm.com"
    assert claims["roles"] == ["analyst", "auditor"]
    assert 0 < claims["exp"] - datetime.now(timezone.utc).timestamp() <= 300


# --------------------------------------------------------------------------
# The user row is authoritative on every request (nothing is cached)
# --------------------------------------------------------------------------


def _set_user(session, email: str, **changes):
    from sqlmodel import select

    from backend.models.app_user import AppUser

    row = session.exec(select(AppUser).where(AppUser.email == email)).one()
    for key, value in changes.items():
        setattr(row, key, value)
    session.add(row)
    session.commit()
    return row


def test_deactivated_users_valid_token_is_rejected_at_once(session):
    headers = auth_headers("alice@firm.com")
    assert anon.get(READ_URL, headers=headers).status_code == 200

    _set_user(session, "alice@firm.com", is_active=False)

    resp = anon.get(READ_URL, headers=headers)  # same, still-unexpired token
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"] == "Bearer"
    assert "no longer active" in resp.json()["detail"]


def test_reactivating_a_user_restores_access_with_the_same_token(session):
    headers = auth_headers("alice@firm.com")
    _set_user(session, "alice@firm.com", is_active=False)
    assert anon.get(READ_URL, headers=headers).status_code == 401

    _set_user(session, "alice@firm.com", is_active=True)

    assert anon.get(READ_URL, headers=headers).status_code == 200


def test_deleted_users_valid_token_is_rejected(session):
    from sqlmodel import select

    from backend.models.app_user import AppUser

    headers = auth_headers("bob@firm.com")
    session.delete(session.exec(select(AppUser).where(AppUser.email == "bob@firm.com")).one())
    session.commit()

    assert anon.get(READ_URL, headers=headers).status_code == 401


def test_role_removed_in_the_db_takes_effect_on_the_next_request(session):
    # Minted while alice is an analyst, so the token itself says "analyst".
    headers = auth_headers("alice@firm.com", roles=("analyst",))
    assert anon.post(DECIDE_URL, json=DECIDE_BODY, headers=headers).status_code == 400  # past the role gate

    _set_user(session, "alice@firm.com", roles="")

    resp = anon.post(DECIDE_URL, json=DECIDE_BODY, headers=headers)
    assert resp.status_code == 403
    assert "analyst" in resp.json()["detail"]
    assert anon.post("/valuation/run", json=RUN_BODY, headers=headers).status_code == 403
    assert anon.get(READ_URL, headers=headers).status_code == 200  # still signed in, just demoted


def test_role_granted_in_the_db_works_with_a_token_that_has_no_roles(session):
    headers = _forge(_valid_claims(sub="viewer@firm.com", roles=None))
    assert anon.post(DECIDE_URL, json=DECIDE_BODY, headers=headers).status_code == 403

    _set_user(session, "viewer@firm.com", roles="analyst")

    assert anon.post(DECIDE_URL, json=DECIDE_BODY, headers=headers).status_code == 400  # past the role gate


def test_junk_roles_claim_is_ignored_because_the_db_decides():
    headers = _forge(_valid_claims(roles="analyst"))  # a string, not a list: previously a 401
    assert anon.get(READ_URL, headers=headers).status_code == 200


def test_subject_is_matched_case_insensitively_and_recorded_canonically(session):
    company = get_or_create_company(session, ticker="AAPL", cik10="0000320193", name="Apple Inc.")
    pending = approval.propose(
        session, proposed_by="proposer@firm.com", company_id=company.id, assumption_key="wacc",
        ai_recommended_value=0.09, ai_rationale="x",
    )

    resp = anon.post(
        f"/valuation/assumptions/{pending.id}/decide", json=DECIDE_BODY, headers=_forge(_valid_claims(sub="ALICE@Firm.com"))
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["decided_by"] == "alice@firm.com"  # the row's email, not the token's spelling


def test_a_deactivated_user_cannot_use_a_blocked_decide_either(session):
    company = get_or_create_company(session, ticker="AAPL", cik10="0000320193", name="Apple Inc.")
    pending = approval.propose(
        session, proposed_by="proposer@firm.com", company_id=company.id, assumption_key="wacc",
        ai_recommended_value=0.09, ai_rationale="x",
    )
    headers = auth_headers("alice@firm.com")
    _set_user(session, "alice@firm.com", is_active=False)

    resp = anon.post(f"/valuation/assumptions/{pending.id}/decide", json=DECIDE_BODY, headers=headers)

    assert resp.status_code == 401
    session.refresh(pending)
    assert pending.status == AssumptionStatus.PENDING
