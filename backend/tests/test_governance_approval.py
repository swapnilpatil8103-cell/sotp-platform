"""Unit tests for backend/governance/approval.py — the human judgment gate."""

from __future__ import annotations

import pytest

from backend.data.persistence import get_or_create_company
from backend.governance import approval
from backend.models.enums import AssumptionStatus


def _propose(session, company_id):
    return approval.propose(
        session,
        company_id=company_id,
        assumption_key="wacc",
        ai_recommended_value=0.09,
        ai_rationale="Based on peer betas and current risk-free rate.",
        ai_confidence=0.7,
        subject="WACC for DCF",
    )


def test_propose_creates_pending_decision(session):
    company = get_or_create_company(session, ticker="AAPL", cik10="0000320193", name="Apple Inc.")
    decision = _propose(session, company.id)

    assert decision.id is not None
    assert decision.status == AssumptionStatus.PENDING
    assert decision.ai_recommended_value == 0.09
    assert decision.approved_value is None
    assert decision.valuation_run_id is None


def test_approve_flow_retains_both_ai_and_human_values(session):
    company = get_or_create_company(session, ticker="AAPL", cik10="0000320193", name="Apple Inc.")
    decision = _propose(session, company.id)

    approved = approval.record_decision(
        session, decision_id=decision.id, decision="APPROVE", user_id="analyst@firm.com"
    )

    assert approved.status == AssumptionStatus.APPROVED
    assert approved.approved_value == 0.09
    assert approved.ai_recommended_value == 0.09  # untouched original
    assert approved.decided_by == "analyst@firm.com"
    assert approved.decided_at is not None


def test_edit_flow_records_distinct_human_value_and_reason(session):
    company = get_or_create_company(session, ticker="AAPL", cik10="0000320193", name="Apple Inc.")
    decision = _propose(session, company.id)

    edited = approval.record_decision(
        session,
        decision_id=decision.id,
        decision="EDIT",
        user_id="analyst@firm.com",
        human_value=0.095,
        reason="Peer beta set looked stale; bumped WACC 50bps.",
    )

    assert edited.status == AssumptionStatus.OVERRIDDEN
    assert edited.approved_value == 0.095
    assert edited.ai_recommended_value == 0.09  # original AI value still visible
    assert edited.approval_reason == "Peer beta set looked stale; bumped WACC 50bps."


def test_edit_without_human_value_raises(session):
    company = get_or_create_company(session, ticker="AAPL", cik10="0000320193", name="Apple Inc.")
    decision = _propose(session, company.id)

    with pytest.raises(approval.GovernanceError):
        approval.record_decision(session, decision_id=decision.id, decision="EDIT", user_id="analyst@firm.com")


def test_reject_flow_leaves_no_usable_value(session):
    company = get_or_create_company(session, ticker="AAPL", cik10="0000320193", name="Apple Inc.")
    decision = _propose(session, company.id)

    rejected = approval.record_decision(
        session,
        decision_id=decision.id,
        decision="REJECT",
        user_id="analyst@firm.com",
        reason="Not grounded in current filings.",
    )

    assert rejected.status == AssumptionStatus.REJECTED
    assert rejected.approved_value is None
    assert rejected.ai_recommended_value == 0.09  # AI value still visible for audit


def test_cannot_redecide_an_already_decided_row(session):
    company = get_or_create_company(session, ticker="AAPL", cik10="0000320193", name="Apple Inc.")
    decision = _propose(session, company.id)
    approval.record_decision(session, decision_id=decision.id, decision="APPROVE", user_id="analyst@firm.com")

    with pytest.raises(approval.GovernanceError):
        approval.record_decision(session, decision_id=decision.id, decision="APPROVE", user_id="analyst@firm.com")


def test_list_pending_and_all_for_company(session):
    company = get_or_create_company(session, ticker="AAPL", cik10="0000320193", name="Apple Inc.")
    d1 = _propose(session, company.id)
    d2 = approval.propose(
        session,
        company_id=company.id,
        assumption_key="terminal_growth_rate",
        ai_recommended_value=0.025,
        ai_rationale="Long-run GDP-ish growth.",
    )
    approval.record_decision(session, decision_id=d1.id, decision="APPROVE", user_id="analyst@firm.com")

    pending = approval.list_pending_for_company(session, company.id)
    assert [p.id for p in pending] == [d2.id]

    all_decisions = approval.list_decisions_for_company(session, company.id)
    assert {d.id for d in all_decisions} == {d1.id, d2.id}


def test_governance_module_never_imports_valuation_engine():
    """Structural guardrail: backend/governance/approval.py must not import
    from backend.valuation -- it records decisions only, it never executes
    financial calculations off the strength of an AI recommendation alone."""
    import backend.governance.approval as approval_module

    source = open(approval_module.__file__, encoding="utf-8").read()
    assert "backend.valuation" not in source
    assert "from backend import valuation" not in source
