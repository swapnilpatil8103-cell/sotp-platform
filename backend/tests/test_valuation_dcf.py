"""Unit tests for backend.valuation.dcf -- verifies FCFF, terminal value,
EV->equity bridge, and a full multi-year projection against an independently
computed expected output (same formulas, computed in plain Python here
rather than by calling the module, so the test is a genuine cross-check)."""

from __future__ import annotations

import pytest

from backend.schemas.valuation import DcfInput
from backend.valuation.dcf import compute_terminal_value, run_dcf


def test_terminal_value_gordon_growth():
    # TV = FCFF_(n+1) / (wacc - g); FCFF_(n+1) = final_fcff * (1+g)
    final_fcff = 100.0
    wacc = 0.10
    g = 0.03
    tv = compute_terminal_value(final_fcff, wacc, g)
    expected = (100.0 * 1.03) / (0.10 - 0.03)
    assert tv == pytest.approx(expected)


def test_terminal_value_requires_wacc_above_growth():
    with pytest.raises(ValueError):
        compute_terminal_value(100.0, 0.05, 0.05)
    with pytest.raises(ValueError):
        compute_terminal_value(100.0, 0.04, 0.05)


def _independent_dcf(inputs: DcfInput) -> dict:
    """Recompute the whole DCF independently, in plain Python, from the same
    formulas the module documents -- used to cross-check run_dcf's output."""
    revenue = inputs.base_revenue
    fcffs = []
    discount_factors = []
    for i in range(len(inputs.revenue_growth_rates)):
        revenue = revenue * (1 + inputs.revenue_growth_rates[i])
        ebit = revenue * inputs.ebit_margins[i]
        nopat = ebit * (1 - inputs.tax_rate)
        da = revenue * inputs.da_pct_of_revenue[i]
        capex = revenue * inputs.capex_pct_of_revenue[i]
        nwc_change = revenue * inputs.nwc_change_pct_of_revenue[i]
        fcff = nopat + da - capex - nwc_change
        fcffs.append(fcff)
        discount_factors.append(1.0 / ((1 + inputs.wacc) ** (i + 1)))

    sum_pv_fcff = sum(f * d for f, d in zip(fcffs, discount_factors))
    tv = (fcffs[-1] * (1 + inputs.terminal_growth_rate)) / (
        inputs.wacc - inputs.terminal_growth_rate
    )
    pv_tv = tv * discount_factors[-1]
    ev = sum_pv_fcff + pv_tv
    equity = ev - inputs.net_debt + inputs.cash_and_equivalents + inputs.investments - inputs.minority_interest
    price = equity / inputs.diluted_shares_outstanding
    return {
        "fcffs": fcffs,
        "enterprise_value": ev,
        "equity_value": equity,
        "implied_price_per_share": price,
        "terminal_value_undiscounted": tv,
        "terminal_value_discounted": pv_tv,
    }


def _make_two_year_inputs() -> DcfInput:
    return DcfInput(
        base_revenue=1000.0,
        revenue_growth_rates=[0.10, 0.08],
        ebit_margins=[0.20, 0.20],
        tax_rate=0.25,
        da_pct_of_revenue=[0.05, 0.05],
        capex_pct_of_revenue=[0.06, 0.06],
        nwc_change_pct_of_revenue=[0.01, 0.01],
        wacc=0.10,
        terminal_growth_rate=0.03,
        net_debt=200.0,
        cash_and_equivalents=100.0,
        investments=0.0,
        minority_interest=0.0,
        diluted_shares_outstanding=100.0,
    )


def test_dcf_year_one_fcff_hand_calculated():
    inputs = _make_two_year_inputs()
    result = run_dcf(inputs)

    y1 = result.projections[0]
    # revenue = 1000 * 1.10 = 1100
    # ebit = 1100 * 0.20 = 220; nopat = 220 * 0.75 = 165
    # da = 1100*0.05 = 55; capex = 1100*0.06 = 66; nwc_change = 1100*0.01 = 11
    # fcff = 165 + 55 - 66 - 11 = 143
    assert y1.revenue == pytest.approx(1100.0)
    assert y1.ebit == pytest.approx(220.0)
    assert y1.nopat == pytest.approx(165.0)
    assert y1.fcff == pytest.approx(143.0)
    assert y1.discount_factor == pytest.approx(1 / 1.10)
    assert y1.discounted_fcff == pytest.approx(143.0 / 1.10)


def test_dcf_full_projection_matches_independent_recalculation():
    inputs = _make_two_year_inputs()
    result = run_dcf(inputs)
    expected = _independent_dcf(inputs)

    assert result.enterprise_value == pytest.approx(expected["enterprise_value"])
    assert result.equity_value == pytest.approx(expected["equity_value"])
    assert result.implied_price_per_share == pytest.approx(expected["implied_price_per_share"])
    assert result.terminal_value_undiscounted == pytest.approx(expected["terminal_value_undiscounted"])
    assert result.terminal_value_discounted == pytest.approx(expected["terminal_value_discounted"])

    # Equity value bridge sanity: equity = EV - net_debt + cash + investments - minority
    assert result.equity_value == pytest.approx(
        result.enterprise_value - inputs.net_debt + inputs.cash_and_equivalents
        + inputs.investments - inputs.minority_interest
    )


def test_dcf_mismatched_schedule_lengths_raises():
    with pytest.raises(ValueError):
        DcfInput(
            base_revenue=1000.0,
            revenue_growth_rates=[0.1, 0.1],
            ebit_margins=[0.2],  # wrong length
            tax_rate=0.25,
            da_pct_of_revenue=[0.05, 0.05],
            capex_pct_of_revenue=[0.06, 0.06],
            nwc_change_pct_of_revenue=[0.01, 0.01],
            wacc=0.10,
            terminal_growth_rate=0.03,
            net_debt=0.0,
            cash_and_equivalents=0.0,
            diluted_shares_outstanding=100.0,
        ).validate_lengths()
