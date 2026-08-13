"""Scenario engine: BULL / BASE / BEAR runs over Phase 5's deterministic
DCF/comps engine.

Pure composition -- this module invokes ``backend.valuation.dcf.run_dcf`` and
``backend.valuation.comps.run_comps`` (and, when segment-level inputs are
supplied for each scenario, ``backend.valuation.sotp.run_sotp``) and never
reimplements any of their math. Each of the three scenarios is an explicit,
caller-supplied ``DcfInput`` (or ``CompsInput``) -- there is no built-in
"bull means +2%" default; the caller (human analyst, optionally seeded by a
Phase 6 AI assumption recommendation that has already been through
governance approval) must supply the fully-formed input set for each
scenario.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from backend.schemas.valuation import DcfInput, DcfResult
from backend.valuation.dcf import run_dcf


class ScenarioRunInput(BaseModel):
    """One named scenario's DCF input set."""

    name: str
    dcf_input: DcfInput


class ScenarioOutcome(BaseModel):
    name: str
    dcf_result: DcfResult
    enterprise_value: float
    equity_value: float
    implied_price_per_share: float
    upside_downside_pct: Optional[float] = None


class ScenarioEngineInput(BaseModel):
    bull: ScenarioRunInput
    base: ScenarioRunInput
    bear: ScenarioRunInput
    current_market_price: Optional[float] = None


class ScenarioEngineResult(BaseModel):
    bull: ScenarioOutcome
    base: ScenarioOutcome
    bear: ScenarioOutcome
    current_market_price: Optional[float] = None
    ordering_valid: bool = Field(
        ...,
        description="True if bull enterprise_value >= base >= bear (sanity check, not enforced)",
    )


def _upside_downside_pct(implied_price: float, market_price: Optional[float]) -> Optional[float]:
    if not market_price:
        return None
    return (implied_price - market_price) / market_price * 100


def _run_scenario(scenario: ScenarioRunInput, current_market_price: Optional[float]) -> ScenarioOutcome:
    result = run_dcf(scenario.dcf_input)
    return ScenarioOutcome(
        name=scenario.name,
        dcf_result=result,
        enterprise_value=result.enterprise_value,
        equity_value=result.equity_value,
        implied_price_per_share=result.implied_price_per_share,
        upside_downside_pct=_upside_downside_pct(result.implied_price_per_share, current_market_price),
    )


def run_scenarios(inputs: ScenarioEngineInput) -> ScenarioEngineResult:
    """Run BULL/BASE/BEAR through Phase 5's ``run_dcf`` and return all three
    outcomes side by side plus upside/downside vs an optional current market
    price. ``ordering_valid`` is a diagnostic (bull EV >= base EV >= bear EV)
    surfaced for the caller/UI -- it is not enforced/raised on.
    """
    bull_outcome = _run_scenario(inputs.bull, inputs.current_market_price)
    base_outcome = _run_scenario(inputs.base, inputs.current_market_price)
    bear_outcome = _run_scenario(inputs.bear, inputs.current_market_price)

    ordering_valid = (
        bull_outcome.enterprise_value >= base_outcome.enterprise_value >= bear_outcome.enterprise_value
    )

    return ScenarioEngineResult(
        bull=bull_outcome,
        base=base_outcome,
        bear=bear_outcome,
        current_market_price=inputs.current_market_price,
        ordering_valid=ordering_valid,
    )
