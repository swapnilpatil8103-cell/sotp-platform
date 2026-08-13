"""Tests for backend/valuation/reverse_valuation.py -- deterministic
scipy.optimize round-trip solve + classification thresholds."""

from __future__ import annotations

import pytest

from backend.schemas.valuation import DcfInput
from backend.valuation.dcf import run_dcf
from backend.valuation.reverse_valuation import (
    RangeInput,
    ReverseValuationInput,
    classify_assumption,
    solve_reverse_valuation,
)


def _base_dcf_input(revenue_growth: float = 0.08, terminal_growth: float = 0.03) -> DcfInput:
    return DcfInput(
        base_revenue=1000,
        revenue_growth_rates=[revenue_growth] * 5,
        ebit_margins=[0.20] * 5,
        tax_rate=0.25,
        da_pct_of_revenue=[0.05] * 5,
        capex_pct_of_revenue=[0.06] * 5,
        nwc_change_pct_of_revenue=[0.01] * 5,
        wacc=0.09,
        terminal_growth_rate=terminal_growth,
        net_debt=100,
        cash_and_equivalents=50,
        diluted_shares_outstanding=100,
    )


def test_round_trip_solve_revenue_growth():
    known_growth = 0.08
    base = _base_dcf_input(revenue_growth=known_growth)
    target_ev = run_dcf(base).enterprise_value

    inputs = ReverseValuationInput(
        base_dcf_input=base,
        target_enterprise_value=target_ev,
        solve_target="revenue_growth",
        search_low=0.0,
        search_high=0.30,
        historical_range=RangeInput(low=0.03, high=0.10),
        peer_range=RangeInput(low=0.05, high=0.12),
    )
    result = solve_reverse_valuation(inputs)
    assert result.implied_value == pytest.approx(known_growth, abs=1e-6)
    assert result.achieved_enterprise_value == pytest.approx(target_ev, rel=1e-6)


def test_round_trip_solve_terminal_growth():
    known_terminal_growth = 0.03
    base = _base_dcf_input(terminal_growth=known_terminal_growth)
    target_ev = run_dcf(base).enterprise_value

    inputs = ReverseValuationInput(
        base_dcf_input=base,
        target_enterprise_value=target_ev,
        solve_target="terminal_growth",
        search_low=0.0,
        search_high=0.08,
        historical_range=RangeInput(low=0.01, high=0.04),
        peer_range=RangeInput(low=0.02, high=0.05),
    )
    result = solve_reverse_valuation(inputs)
    assert result.implied_value == pytest.approx(known_terminal_growth, abs=1e-6)
    assert result.achieved_enterprise_value == pytest.approx(target_ev, rel=1e-6)


def test_unbracketed_target_raises():
    base = _base_dcf_input()
    inputs = ReverseValuationInput(
        base_dcf_input=base,
        target_enterprise_value=1e12,  # unreachable within bounds
        solve_target="revenue_growth",
        search_low=0.0,
        search_high=0.10,
        historical_range=RangeInput(low=0.03, high=0.10),
        peer_range=RangeInput(low=0.05, high=0.12),
    )
    with pytest.raises(ValueError):
        solve_reverse_valuation(inputs)


@pytest.mark.parametrize(
    "implied,expected",
    [
        (0.02, "REASONABLE"),  # combined range [0.02, 0.10] roughly, at floor
        (0.06, "REASONABLE"),  # middle of range
        (0.10, "REASONABLE"),  # at ceiling
        (0.13, "AGGRESSIVE"),  # above range, within 50% of width
        (0.20, "EXTREME"),  # well above range
        (-0.03, "EXTREME"),  # far below range
    ],
)
def test_classify_assumption_thresholds(implied, expected):
    historical = RangeInput(low=0.02, high=0.08)
    peer = RangeInput(low=0.04, high=0.10)
    _, classification = classify_assumption(implied, historical, peer)
    assert classification == expected


def test_classify_assumption_conservative_just_below_range():
    historical = RangeInput(low=0.02, high=0.08)
    peer = RangeInput(low=0.04, high=0.10)
    # combined range [0.02, 0.10], width 0.08; position -0.25 (within -0.5..0) -> CONSERVATIVE
    implied = 0.0
    _, classification = classify_assumption(implied, historical, peer)
    assert classification == "CONSERVATIVE"
