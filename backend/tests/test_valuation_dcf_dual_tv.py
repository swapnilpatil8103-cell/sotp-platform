"""Unit tests for the optional dual Exit Multiple terminal value on
backend.valuation.dcf.run_dcf -- verifies Gordon Growth output is byte-for-
byte unchanged when exit_multiple is omitted, and that the exit-multiple
figures are computed correctly and independently when supplied."""

from __future__ import annotations

import pytest

from backend.schemas.valuation import DcfInput
from backend.valuation.dcf import compute_exit_multiple_terminal_value, run_dcf


def _base_kwargs() -> dict:
    return dict(
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


def test_compute_exit_multiple_terminal_value_hand_calculated():
    assert compute_exit_multiple_terminal_value(500.0, 8.0) == pytest.approx(4000.0)


def test_gordon_growth_unchanged_when_exit_multiple_omitted():
    inputs_without = DcfInput(**_base_kwargs())
    result_without = run_dcf(inputs_without)

    inputs_with_none = DcfInput(**_base_kwargs(), exit_multiple=None)
    result_with_none = run_dcf(inputs_with_none)

    assert result_without.enterprise_value == pytest.approx(result_with_none.enterprise_value)
    assert result_without.equity_value == pytest.approx(result_with_none.equity_value)
    assert result_without.implied_price_per_share == pytest.approx(result_with_none.implied_price_per_share)
    assert result_without.terminal_value_undiscounted == pytest.approx(result_with_none.terminal_value_undiscounted)
    assert result_without.terminal_value_discounted == pytest.approx(result_with_none.terminal_value_discounted)

    # No exit-multiple fields populated when not requested.
    assert result_without.terminal_year_ebitda is None
    assert result_without.exit_multiple_enterprise_value is None
    assert result_without.exit_multiple_equity_value is None
    assert result_without.exit_multiple_implied_price_per_share is None


def test_exit_multiple_terminal_value_computed_independently_alongside_gordon_growth():
    inputs = DcfInput(**_base_kwargs(), exit_multiple=8.0)
    result = run_dcf(inputs)

    # Gordon Growth fields still present and correct (independent formula).
    final_fcff = result.projections[-1].fcff
    expected_tv_gg = (final_fcff * 1.03) / (0.10 - 0.03)
    assert result.terminal_value_undiscounted == pytest.approx(expected_tv_gg)

    # Exit multiple: terminal-year EBITDA = EBIT + D&A of the final projected year.
    final_year = result.projections[-1]
    expected_ebitda = final_year.ebit + final_year.da
    assert result.terminal_year_ebitda == pytest.approx(expected_ebitda)

    expected_exit_tv_undiscounted = expected_ebitda * 8.0
    assert result.exit_multiple_terminal_value_undiscounted == pytest.approx(expected_exit_tv_undiscounted)

    expected_exit_tv_discounted = expected_exit_tv_undiscounted * final_year.discount_factor
    assert result.exit_multiple_terminal_value_discounted == pytest.approx(expected_exit_tv_discounted)

    sum_pv_fcff = sum(p.discounted_fcff for p in result.projections)
    expected_exit_ev = sum_pv_fcff + expected_exit_tv_discounted
    assert result.exit_multiple_enterprise_value == pytest.approx(expected_exit_ev)

    expected_exit_equity = expected_exit_ev - inputs.net_debt + inputs.cash_and_equivalents + inputs.investments - inputs.minority_interest
    assert result.exit_multiple_equity_value == pytest.approx(expected_exit_equity)
    assert result.exit_multiple_implied_price_per_share == pytest.approx(expected_exit_equity / inputs.diluted_shares_outstanding)

    # Exit-multiple EV differs from Gordon Growth EV (independent methods, not the same number).
    assert result.exit_multiple_enterprise_value != pytest.approx(result.enterprise_value)
