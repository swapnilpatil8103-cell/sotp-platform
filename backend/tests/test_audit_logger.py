"""Unit tests for backend/audit/logger.py — log + retrieval round-trip."""

from __future__ import annotations

import time

from backend.audit.logger import (
    ACTION_AI_PROPOSAL_GENERATED,
    ACTION_DECISION_RECORDED,
    ACTION_VALUATION_RUN_EXECUTED,
    ACTOR_AI,
    actor_human,
    get_audit_trail,
    log_event,
)
from backend.data.persistence import get_or_create_company
from backend.governance import approval
from backend.governance.valuation_run import execute_valuation_run
from backend.models.enums import ValuationMethod


def test_log_and_retrieve_round_trip(session):
    entry = log_event(
        session,
        entity_type="Company",
        entity_id=1,
        action="TEST_EVENT",
        actor=ACTOR_AI,
        detail={"foo": "bar"},
    )
    assert entry.id is not None

    trail = get_audit_trail(session, entity_type="Company", entity_id=1)
    assert len(trail) == 1
    assert trail[0]["action"] == "TEST_EVENT"
    assert trail[0]["actor"] == ACTOR_AI
    assert trail[0]["detail"] == {"foo": "bar"}


def test_chronological_ordering(session):
    log_event(session, entity_type="Company", entity_id=5, action="A", actor="system", detail={})
    time.sleep(0.01)
    log_event(session, entity_type="Company", entity_id=5, action="B", actor="system", detail={})
    time.sleep(0.01)
    log_event(session, entity_type="Company", entity_id=5, action="C", actor="system", detail={})

    trail = get_audit_trail(session, entity_type="Company", entity_id=5)
    assert [e["action"] for e in trail] == ["A", "B", "C"]
    timestamps = [e["created_at"] for e in trail]
    assert timestamps == sorted(timestamps)


def test_actor_attribution_ai_vs_human_vs_system(session):
    company = get_or_create_company(session, ticker="AAPL", cik10="0000320193", name="Apple Inc.")

    decision = approval.propose(
        session, company_id=company.id, assumption_key="wacc", ai_recommended_value=0.09, ai_rationale="x"
    )
    approval.record_decision(session, decision_id=decision.id, decision="APPROVE", user_id="analyst@firm.com")
    execute_valuation_run(
        session,
        company_id=company.id,
        method=ValuationMethod.DCF,
        decision_ids=[decision.id],
        inputs={"wacc": 0.09},
        outputs={"per_share_value": 100.0},
        created_by="analyst@firm.com",
    )

    trail = get_audit_trail(session, company_id=company.id)
    actions = [e["action"] for e in trail]
    assert ACTION_AI_PROPOSAL_GENERATED in actions
    assert ACTION_DECISION_RECORDED in actions
    assert ACTION_VALUATION_RUN_EXECUTED in actions

    by_action = {e["action"]: e for e in trail}
    assert by_action[ACTION_AI_PROPOSAL_GENERATED]["actor"] == ACTOR_AI
    assert by_action[ACTION_DECISION_RECORDED]["actor"] == actor_human("analyst@firm.com")
    assert by_action[ACTION_VALUATION_RUN_EXECUTED]["actor"] == actor_human("analyst@firm.com")

    # chronological: proposal must precede decision must precede run
    order = [actions.index(a) for a in (ACTION_AI_PROPOSAL_GENERATED, ACTION_DECISION_RECORDED, ACTION_VALUATION_RUN_EXECUTED)]
    assert order == sorted(order)


def test_get_audit_trail_reconstructs_claim_to_output_story(session):
    """CLAIM -> SOURCE -> INPUT -> CALCULATION -> OUTPUT -> AI INTERPRETATION:
    verify the retrieval function tags each entry with a role so a caller can
    walk that chain, even though the underlying storage doesn't literally
    have six columns."""
    company = get_or_create_company(session, ticker="MSFT", cik10="0000789019", name="Microsoft Corp.")
    decision = approval.propose(
        session, company_id=company.id, assumption_key="terminal_growth_rate", ai_recommended_value=0.025, ai_rationale="x"
    )
    approval.record_decision(session, decision_id=decision.id, decision="APPROVE", user_id="a@b.com")
    execute_valuation_run(
        session,
        company_id=company.id,
        method=ValuationMethod.DCF,
        decision_ids=[decision.id],
        inputs={"terminal_growth_rate": 0.025},
        outputs={"per_share_value": 300.0},
        created_by="a@b.com",
    )

    trail = get_audit_trail(session, company_id=company.id)
    roles = {e["action"]: e["role"] for e in trail}
    assert roles[ACTION_AI_PROPOSAL_GENERATED] == "AI_INTERPRETATION"
    assert roles[ACTION_DECISION_RECORDED] == "INPUT"
    assert roles[ACTION_VALUATION_RUN_EXECUTED] == "CALCULATION_AND_OUTPUT"
