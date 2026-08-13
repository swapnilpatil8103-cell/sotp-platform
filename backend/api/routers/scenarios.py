"""Phase 8 endpoints: scenario engine, reverse valuation, value-unlock
proposal + compute, risk dashboard.

Follows the same "pure computation, caller assembles inputs" convention as
POST /valuation/sotp: these endpoints do not read/write the DB themselves
(except value-unlock's AI-proposal step, which persists a governance
AssumptionDecision via backend.governance.approval.propose the same way
Phase 7's /valuation/assumptions/propose does) and never invoke AI directly
except through the explicit, typed backend.ai.tasks functions -- and only
where the spec allows AI (value-unlock idea proposal, risk explanation,
reverse-valuation narrative explanation). No endpoint here lets AI output
flow into a dollar figure without a human approval step in between.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from backend.ai.adapter import AIAdapter
from backend.ai.errors import AIError
from backend.ai.gemini_adapter import GeminiAdapter
from backend.ai.tasks.reverse_valuation_explainer import explain_reverse_valuation
from backend.ai.tasks.risk_explanation import score_strategic_and_execution_risk
from backend.ai.tasks.value_unlock_ideas import ACTION_TYPE_CODES, propose_value_unlock_ideas
from backend.db import get_session
from backend.governance import approval
from backend.valuation import value_unlock as vu
from backend.valuation.reverse_valuation import ReverseValuationInput, ReverseValuationResult, solve_reverse_valuation
from backend.valuation.risk_dashboard import RiskDashboardResult, build_risk_dashboard
from backend.valuation.scenarios import ScenarioEngineInput, ScenarioEngineResult, run_scenarios

router = APIRouter(prefix="/scenarios", tags=["scenarios"])


def _get_adapter() -> AIAdapter:
    return GeminiAdapter()


# --------------------------------------------------------------------------
# Scenario engine
# --------------------------------------------------------------------------


@router.post("/run", response_model=ScenarioEngineResult)
def run_scenario_engine(payload: ScenarioEngineInput):
    try:
        return run_scenarios(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# --------------------------------------------------------------------------
# Reverse valuation
# --------------------------------------------------------------------------


@router.post("/reverse-valuation", response_model=ReverseValuationResult)
def reverse_valuation(payload: ReverseValuationInput):
    try:
        return solve_reverse_valuation(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/reverse-valuation/explain")
def reverse_valuation_explain(result: dict[str, Any], adapter: AIAdapter = Depends(_get_adapter)):
    """Optional AI narrative explanation of an already-computed reverse
    valuation result. Never recomputes the number; discards (abstains on)
    any AI text containing an untraceable number."""
    try:
        return explain_reverse_valuation(adapter, result)
    except AIError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


# --------------------------------------------------------------------------
# Value-unlock: AI proposes actions (requires human approval) -> compute
# --------------------------------------------------------------------------


@router.post("/value-unlock/propose")
def propose_value_unlock(
    company_id: int,
    sotp_breakdown: dict[str, Any],
    conglomerate_discount_pct: Optional[float] = None,
    adapter: AIAdapter = Depends(_get_adapter),
    session: Session = Depends(get_session),
):
    """AI proposes which structural actions might be worth modeling. Each
    idea is recorded as a PENDING AssumptionDecision (Phase 7's governance
    pattern) -- no dollar figure is computed here or ever by the AI; the
    `ai_recommended_value` column stores only a stable categorical code for
    the action_type (see ACTION_TYPE_CODES), never a valuation number."""
    try:
        result = propose_value_unlock_ideas(adapter, sotp_breakdown, conglomerate_discount_pct)
    except AIError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    if result.abstained:
        return {"abstained": True, "reason": result.reason, "proposals": []}

    proposals = []
    for idea in result.ideas:
        decision = approval.propose(
            session,
            company_id=company_id,
            assumption_key=f"value_unlock_action:{idea.action_type}",
            ai_recommended_value=ACTION_TYPE_CODES[idea.action_type],
            ai_rationale=idea.rationale,
            subject=idea.target_segment or idea.action_type,
        )
        proposals.append({"decision_id": decision.id, "action_type": idea.action_type, "target_segment": idea.target_segment, "rationale": idea.rationale})

    return {"abstained": False, "proposals": proposals}


@router.post("/value-unlock/compute/{action_type}")
def compute_value_unlock(action_type: str, decision_id: int, params: dict[str, Any], session: Session = Depends(get_session)):
    """Compute the deterministic dollar uplift for a proposed action --
    gated on the proposal (decision_id) having been APPROVED/OVERRIDDEN by a
    human first, exactly like Phase 7's governed valuation run. `params`
    supplies the explicit, human-provided numeric inputs (multiples,
    proceeds, amounts) the deterministic module requires for this
    action_type -- never invented defaults."""
    decision = approval.get_decision(session, decision_id)
    if decision is None:
        raise HTTPException(status_code=404, detail=f"No AssumptionDecision with id={decision_id}")
    if decision.status.value not in ("APPROVED", "OVERRIDDEN"):
        raise HTTPException(
            status_code=400,
            detail=f"AssumptionDecision {decision_id} has status={decision.status}; a human must approve it before computing dollar figures.",
        )

    try:
        if action_type in ("spin_off", "segment_separation"):
            return vu.compute_spin_off_or_separation(action_type=action_type, **params)
        elif action_type == "subsidiary_ipo":
            return vu.compute_subsidiary_ipo(**params)
        elif action_type == "asset_sale":
            return vu.compute_asset_sale(**params)
        elif action_type == "buyback":
            return vu.compute_buyback(**params)
        elif action_type == "debt_reduction":
            return vu.compute_debt_reduction(**params)
        elif action_type == "special_dividend":
            return vu.compute_special_dividend(**params)
        else:
            raise HTTPException(status_code=400, detail=f"Unknown action_type '{action_type}'")
    except TypeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid params for '{action_type}': {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# --------------------------------------------------------------------------
# Risk dashboard
# --------------------------------------------------------------------------


@router.post("/risk-dashboard", response_model=RiskDashboardResult)
def risk_dashboard(
    coverage_pct: float,
    methodologies_used: int,
    sensitivity_spread_pct: float,
    forecast_classification: str,
    beta: Optional[float] = None,
    volatility_pct: Optional[float] = None,
    strategic_execution_context: Optional[dict[str, Any]] = None,
    adapter: AIAdapter = Depends(_get_adapter),
):
    strategic_execution_context = strategic_execution_context or {}
    try:
        ai_result = score_strategic_and_execution_risk(adapter, strategic_execution_context)
    except AIError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    if ai_result.abstained or ai_result.strategic_risk is None or ai_result.execution_risk is None:
        raise HTTPException(
            status_code=422,
            detail=f"Could not obtain a valid Strategic/Execution Risk score: {ai_result.reason}",
        )

    try:
        return build_risk_dashboard(
            coverage_pct=coverage_pct,
            methodologies_used=methodologies_used,
            sensitivity_spread_pct=sensitivity_spread_pct,
            forecast_classification=forecast_classification,  # type: ignore[arg-type]
            beta=beta,
            volatility_pct=volatility_pct,
            strategic_risk=ai_result.strategic_risk,
            execution_risk=ai_result.execution_risk,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
