"""Unit tests for backend/governance/valuation_run.py — the gated
orchestration between approved assumptions and a persisted ValuationRun,
plus versioning and the deterministic diff function.

Runs are executed through the real engine (the server computes outputs); see
test_governed_run_integrity.py for the forged-output / mismatch / per-method
tests of the server-side computation itself."""

from __future__ import annotations

import pytest

from backend.data.persistence import get_or_create_company
from backend.governance import approval
from backend.governance.valuation_run import (
    UnapprovedAssumptionError,
    assemble_approved_assumptions,
    diff_runs,
    execute_valuation_run,
    list_versions,
)
from backend.models.enums import ValuationMethod
from backend.tests.fixtures.run_inputs import comps_inputs, dcf_inputs


def _company(session, ticker="AAPL", cik10="0000320193"):
    return get_or_create_company(session, ticker=ticker, cik10=cik10, name="Apple Inc.")


def test_running_with_pending_assumption_is_rejected(session):
    company = _company(session)
    decision = approval.propose(
        session,
        company_id=company.id,
        assumption_key="wacc",
        ai_recommended_value=0.09,
        ai_rationale="peer-based",
    )
    # never decided -- still PENDING

    with pytest.raises(UnapprovedAssumptionError):
        execute_valuation_run(
            session,
            company_id=company.id,
            method=ValuationMethod.DCF,
            decision_ids=[decision.id],
            inputs=dcf_inputs(wacc=0.09),
            created_by="analyst@firm.com",
        )
    assert list_versions(session, company.id) == []


def test_running_with_rejected_assumption_is_rejected(session):
    company = _company(session)
    decision = approval.propose(
        session, company_id=company.id, assumption_key="wacc", ai_recommended_value=0.09, ai_rationale="x"
    )
    approval.record_decision(session, decision_id=decision.id, decision="REJECT", user_id="analyst@firm.com")

    with pytest.raises(UnapprovedAssumptionError):
        execute_valuation_run(
            session,
            company_id=company.id,
            method=ValuationMethod.DCF,
            decision_ids=[decision.id],
            inputs=dcf_inputs(),
            created_by="analyst@firm.com",
        )
    assert list_versions(session, company.id) == []


def test_approved_assumption_allows_run_and_versions_increment(session):
    company = _company(session)
    d1 = approval.propose(
        session, company_id=company.id, assumption_key="wacc", ai_recommended_value=0.09, ai_rationale="x"
    )
    approval.record_decision(session, decision_id=d1.id, decision="APPROVE", user_id="analyst@firm.com")

    run1 = execute_valuation_run(
        session,
        company_id=company.id,
        method=ValuationMethod.DCF,
        decision_ids=[d1.id],
        inputs=dcf_inputs(wacc=0.09),
        created_by="analyst@firm.com",
    )
    assert run1.version == 1

    d2 = approval.propose(
        session, company_id=company.id, assumption_key="wacc", ai_recommended_value=0.10, ai_rationale="y"
    )
    approval.record_decision(
        session, decision_id=d2.id, decision="EDIT", user_id="analyst@firm.com", human_value=0.11, reason="bumped"
    )
    # The EDIT overrode the AI's 0.10 with the human's 0.11; the run must use 0.11.
    run2 = execute_valuation_run(
        session,
        company_id=company.id,
        method=ValuationMethod.DCF,
        decision_ids=[d2.id],
        inputs=dcf_inputs(wacc=0.11),
        created_by="analyst@firm.com",
    )
    assert run2.version == 2

    versions = list_versions(session, company.id, ValuationMethod.DCF)
    assert [v.version for v in versions] == [1, 2]

    # the decision row used gets stamped with the run it fed
    assert d2.valuation_run_id == run2.id


def test_assemble_approved_assumptions_rejects_wrong_company(session):
    company_a = _company(session, ticker="AAPL")
    company_b = _company(session, ticker="MSFT", cik10="0000789019")

    d1 = approval.propose(
        session, company_id=company_a.id, assumption_key="wacc", ai_recommended_value=0.09, ai_rationale="x"
    )
    approval.record_decision(session, decision_id=d1.id, decision="APPROVE", user_id="a@b.com")

    with pytest.raises(UnapprovedAssumptionError):
        assemble_approved_assumptions(session, [d1.id], company_id=company_b.id)


def test_diff_runs_identifies_changed_and_unchanged_fields(session):
    company = _company(session)
    d1 = approval.propose(
        session, company_id=company.id, assumption_key="wacc", ai_recommended_value=0.09, ai_rationale="x"
    )
    approval.record_decision(session, decision_id=d1.id, decision="APPROVE", user_id="a@b.com")

    run1 = execute_valuation_run(
        session,
        company_id=company.id,
        method=ValuationMethod.DCF,
        decision_ids=[d1.id],
        inputs=dcf_inputs(wacc=0.09),
        created_by="a@b.com",
    )

    d2 = approval.propose(
        session, company_id=company.id, assumption_key="wacc", ai_recommended_value=0.10, ai_rationale="x"
    )
    approval.record_decision(session, decision_id=d2.id, decision="APPROVE", user_id="a@b.com")
    run2 = execute_valuation_run(
        session,
        company_id=company.id,
        method=ValuationMethod.DCF,
        decision_ids=[d2.id],
        inputs=dcf_inputs(wacc=0.10),
        created_by="a@b.com",
    )

    diff = diff_runs(run1, run2)
    assert diff["from_version"] == 1
    assert diff["to_version"] == 2

    changed_field_names = {c["field"] for c in diff["changed_fields"]}
    assert "inputs.wacc" in changed_field_names
    # outputs are real engine results, so a higher WACC moves them
    assert "outputs.enterprise_value" in changed_field_names
    assert "outputs.implied_price_per_share" in changed_field_names
    # untouched inputs should not appear as changed
    assert "inputs.base_revenue" not in changed_field_names
    assert diff["unchanged_field_count"] >= 1

    # key highlights subset populated for well-known input/output fields
    key_fields = {c["field"] for c in diff["key_changes"]}
    assert "inputs.wacc" in key_fields
    assert "outputs.enterprise_value" in key_fields
    assert "outputs.implied_price_per_share" in key_fields


def test_diff_runs_no_differences(session):
    company = _company(session)
    d1 = approval.propose(
        session, company_id=company.id, assumption_key="peer_multiple", ai_recommended_value=11.0, ai_rationale="x"
    )
    approval.record_decision(session, decision_id=d1.id, decision="APPROVE", user_id="a@b.com")
    run1 = execute_valuation_run(
        session,
        company_id=company.id,
        method=ValuationMethod.COMPS,
        decision_ids=[d1.id],
        inputs=comps_inputs(chosen_multiple_value=11.0),
        created_by="a@b.com",
    )
    d2 = approval.propose(
        session, company_id=company.id, assumption_key="peer_multiple", ai_recommended_value=11.0, ai_rationale="x"
    )
    approval.record_decision(session, decision_id=d2.id, decision="APPROVE", user_id="a@b.com")
    run2 = execute_valuation_run(
        session,
        company_id=company.id,
        method=ValuationMethod.COMPS,
        decision_ids=[d2.id],
        inputs=comps_inputs(chosen_multiple_value=11.0),
        created_by="a@b.com",
    )

    diff = diff_runs(run1, run2)
    # Only the governance bookkeeping (which decision ids were consumed)
    # differs -- no actual assumption/output value changed.
    assert all(
        c["field"].startswith("inputs._governance_snapshot") for c in diff["changed_fields"]
    ), diff["changed_fields"]
    assert diff["key_changes"] == []
