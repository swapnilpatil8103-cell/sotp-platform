"""Tests for backend/valuation/scenarios.py -- pure composition over
Phase 5's run_dcf, hand-crafted inputs, no network."""

from __future__ import annotations

from backend.schemas.valuation import DcfInput
from backend.valuation.scenarios import ScenarioEngineInput, ScenarioRunInput, run_scenarios


def _dcf_input(growth: float, margin: float) -> DcfInput:
    return DcfInput(
        base_revenue=1000,
        revenue_growth_rates=[growth] * 5,
        ebit_margins=[margin] * 5,
        tax_rate=0.25,
        da_pct_of_revenue=[0.05] * 5,
        capex_pct_of_revenue=[0.06] * 5,
        nwc_change_pct_of_revenue=[0.01] * 5,
        wacc=0.09,
        terminal_growth_rate=0.03,
        net_debt=100,
        cash_and_equivalents=50,
        diluted_shares_outstanding=100,
    )


def _engine_input(current_market_price=None) -> ScenarioEngineInput:
    return ScenarioEngineInput(
        bull=ScenarioRunInput(name="BULL", dcf_input=_dcf_input(0.15, 0.28)),
        base=ScenarioRunInput(name="BASE", dcf_input=_dcf_input(0.08, 0.20)),
        bear=ScenarioRunInput(name="BEAR", dcf_input=_dcf_input(0.02, 0.12)),
        current_market_price=current_market_price,
    )


def test_bull_base_bear_ordering():
    result = run_scenarios(_engine_input())
    assert result.bull.enterprise_value > result.base.enterprise_value > result.bear.enterprise_value
    assert result.bull.equity_value > result.base.equity_value > result.bear.equity_value
    assert result.bull.implied_price_per_share > result.base.implied_price_per_share > result.bear.implied_price_per_share
    assert result.ordering_valid is True


def test_upside_downside_math_against_current_price():
    market_price = 40.0
    result = run_scenarios(_engine_input(current_market_price=market_price))

    for outcome in (result.bull, result.base, result.bear):
        expected = (outcome.implied_price_per_share - market_price) / market_price * 100
        assert outcome.upside_downside_pct == expected

    assert result.current_market_price == market_price


def test_no_market_price_leaves_upside_downside_none():
    result = run_scenarios(_engine_input(current_market_price=None))
    assert result.bull.upside_downside_pct is None
    assert result.base.upside_downside_pct is None
    assert result.bear.upside_downside_pct is None
