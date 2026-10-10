"""Proposals with no recorded proposer: the data migration and the refusal to approve them."""

from __future__ import annotations

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from backend.api.main import app
from backend.data.persistence import get_or_create_company
from backend.governance import approval
from backend.models.assumption_decision import LEGACY_PROPOSER, AssumptionDecision
from backend.models.enums import AssumptionStatus
from backend.tests.fixtures.auth import auth_headers

client = TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def company_id(session):
    return get_or_create_company(session, ticker="AAPL", cik10="0000320193", name="Apple Inc.").id


def _legacy_row(session, company_id: int, proposed_by) -> AssumptionDecision:
    """A row as it exists after the migration (sentinel) or as the old code left it (NULL)."""
    row = AssumptionDecision(
        company_id=company_id,
        assumption_key="wacc",
        subject="wacc",
        ai_recommended_value=0.09,
        ai_rationale="old proposal",
        status=AssumptionStatus.PENDING,
        proposed_by=proposed_by,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _decide(decision_id: int, decision: str, headers=None, **extra):
    return client.post(
        f"/valuation/assumptions/{decision_id}/decide",
        json={"decision": decision, **extra},
        headers=headers or auth_headers("alice@firm.com"),
    )


# --------------------------------------------------------------------------
# record_decision refuses APPROVE / EDIT without a proposer
# --------------------------------------------------------------------------


@pytest.mark.parametrize("proposed_by", [LEGACY_PROPOSER, None], ids=["sentinel", "null"])
@pytest.mark.parametrize("decision, extra", [("APPROVE", {}), ("EDIT", {"human_value": 0.05})])
def test_cannot_approve_or_edit_a_proposal_with_no_proposer(session, company_id, proposed_by, decision, extra):
    row = _legacy_row(session, company_id, proposed_by)

    resp = _decide(row.id, decision, **extra)

    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "re-propose" in detail.lower() and "no recorded proposer" in detail
    session.refresh(row)
    assert row.status == AssumptionStatus.PENDING and row.decided_by is None and row.approved_value is None


def test_a_legacy_proposal_can_still_be_rejected_so_it_can_be_cleared(session, company_id):
    row = _legacy_row(session, company_id, LEGACY_PROPOSER)

    resp = _decide(row.id, "REJECT", reason="superseded")

    assert resp.status_code == 200
    assert resp.json()["status"] == AssumptionStatus.REJECTED.value and resp.json()["approved_value"] is None


def test_re_proposing_gives_a_decidable_proposal(session, company_id):
    legacy = _legacy_row(session, company_id, LEGACY_PROPOSER)
    assert _decide(legacy.id, "APPROVE").status_code == 400

    new = client.post(
        "/valuation/assumptions/propose",
        json={"company_id": company_id, "assumption_key": "wacc", "ai_recommended_value": 0.09, "ai_rationale": "x"},
        headers=auth_headers("bob@firm.com"),
    ).json()
    assert new["proposed_by"] == "bob@firm.com"

    assert _decide(new["id"], "APPROVE", headers=auth_headers("alice@firm.com")).status_code == 200


def test_the_governance_function_itself_enforces_it_not_just_the_router(session, company_id):
    row = _legacy_row(session, company_id, LEGACY_PROPOSER)
    with pytest.raises(approval.MissingProposerError, match="(?i)re-propose"):
        approval.record_decision(session, decision_id=row.id, decision="APPROVE", user_id="alice@firm.com")


@pytest.mark.parametrize("bad", ["", "   ", LEGACY_PROPOSER])
def test_propose_requires_a_real_proposer(session, company_id, bad):
    with pytest.raises(approval.GovernanceError, match="proposed_by"):
        approval.propose(
            session, company_id=company_id, assumption_key="wacc", ai_recommended_value=0.1, ai_rationale="x",
            proposed_by=bad,
        )


def test_propose_cannot_be_called_without_a_proposer(session, company_id):
    with pytest.raises(TypeError):
        approval.propose(  # type: ignore[call-arg]
            session, company_id=company_id, assumption_key="wacc", ai_recommended_value=0.1, ai_rationale="x"
        )


def test_value_unlock_proposals_record_the_caller(session, company_id, monkeypatch):
    """The scenarios route also creates proposals; it must not leave proposed_by empty."""
    from backend.ai.tasks.value_unlock_ideas import ValueUnlockIdea, ValueUnlockIdeasResult
    from backend.api.routers import scenarios

    fake = ValueUnlockIdeasResult(
        abstained=False,
        ideas=[ValueUnlockIdea(action_type="spin_off", target_segment="Cloud", rationale="focus")],
    )
    monkeypatch.setattr(scenarios, "propose_value_unlock_ideas", lambda *a, **k: fake)
    app.dependency_overrides[scenarios._get_adapter] = lambda: object()
    try:
        resp = client.post(
            f"/scenarios/value-unlock/propose?company_id={company_id}",
            json={"Cloud": 500},
            headers=auth_headers("alice@firm.com"),
        )
    finally:
        app.dependency_overrides.pop(scenarios._get_adapter, None)

    assert resp.status_code == 200, resp.text
    decision_id = resp.json()["proposals"][0]["decision_id"]
    row = approval.get_decision(session, decision_id)
    assert row.proposed_by == "alice@firm.com"
    assert _decide(decision_id, "APPROVE", headers=auth_headers("alice@firm.com")).status_code == 403
    assert _decide(decision_id, "APPROVE", headers=auth_headers("bob@firm.com")).status_code == 200


# --------------------------------------------------------------------------
# the Alembic data migration
# --------------------------------------------------------------------------


def _alembic_config(db_url: str) -> Config:
    cfg = Config("backend/alembic.ini")
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def test_migration_backfills_null_proposers_with_the_sentinel_and_downgrades(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'mig.db'}"
    monkeypatch.setenv("DATABASE_URL", url)  # alembic/env.py reads this
    cfg = _alembic_config(url)
    engine = create_engine(url)

    command.upgrade(cfg, "d4e5f6a7b8c9")  # schema has proposed_by, but nothing is backfilled yet
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO company (id, ticker, cik, name, currency, created_at, updated_at) VALUES (1, 'AAPL', '0000320193', 'Apple Inc.', 'USD', '2026-01-01 00:00:00', '2026-01-01 00:00:00')"))
        for i, who in enumerate([None, "alice@firm.com", None], start=1):
            conn.execute(
                text(
                    "INSERT INTO assumption_decision (id, company_id, assumption_key, status, created_at, proposed_by) "
                    "VALUES (:i, 1, 'wacc', 'PENDING', '2026-01-01 00:00:00', :who)"
                ),
                {"i": i, "who": who},
            )

    command.upgrade(cfg, "head")
    with engine.connect() as conn:
        rows = dict(conn.execute(text("SELECT id, proposed_by FROM assumption_decision")).all())
    assert rows == {1: LEGACY_PROPOSER, 2: "alice@firm.com", 3: LEGACY_PROPOSER}  # real proposer untouched

    command.downgrade(cfg, "d4e5f6a7b8c9")
    with engine.connect() as conn:
        rows = dict(conn.execute(text("SELECT id, proposed_by FROM assumption_decision")).all())
    assert rows == {1: None, 2: "alice@firm.com", 3: None}


def test_migration_sentinel_matches_the_application_constant():
    from backend.migrations.versions import e5f6a7b8c9d0_backfill_legacy_proposer as mig

    assert mig.SENTINEL == LEGACY_PROPOSER
