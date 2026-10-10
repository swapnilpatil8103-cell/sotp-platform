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
        total_debt=200.0,
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

    expected_exit_equity = expected_exit_ev - inputs.total_debt + inputs.cash_and_equivalents + inputs.investments - inputs.minority_interest
    assert result.exit_multiple_equity_value == pytest.approx(expected_exit_equity)
    assert result.exit_multiple_implied_price_per_share == pytest.approx(expected_exit_equity / inputs.diluted_shares_outstanding)

    # Exit-multiple EV differs from Gordon Growth EV (independent methods, not the same number).
    assert result.exit_multiple_enterprise_value != pytest.approx(result.enterprise_value)


# --------------------------------------------------------------------------
# mid_year_convention
# --------------------------------------------------------------------------


def test_mid_year_convention_defaults_off_and_matches_explicit_false():
    default = run_dcf(DcfInput(**_base_kwargs()))
    explicit = run_dcf(DcfInput(**_base_kwargs(), mid_year_convention=False))

    assert DcfInput(**_base_kwargs()).mid_year_convention is False
    assert default.enterprise_value == explicit.enterprise_value
    assert [p.discount_factor for p in default.projections] == [1 / 1.1, 1 / 1.1**2]


def test_mid_year_convention_hand_calculated():
    base = run_dcf(DcfInput(**_base_kwargs()))
    mid = run_dcf(DcfInput(**_base_kwargs(), mid_year_convention=True))

    # Discount periods are 0.5 and 1.5.
    assert mid.projections[0].discount_factor == pytest.approx(1 / 1.1**0.5)
    assert mid.projections[1].discount_factor == pytest.approx(1 / 1.1**1.5)
    # FCFFs themselves are unaffected.
    assert [p.fcff for p in mid.projections] == [p.fcff for p in base.projections]

    # Gordon TV (undiscounted) is unchanged; its PV uses N - 0.5.
    assert mid.terminal_value_undiscounted == pytest.approx(base.terminal_value_undiscounted)
    assert mid.terminal_value_discounted == pytest.approx(base.terminal_value_undiscounted / 1.1**1.5)

    expected_ev = sum(p.fcff / 1.1 ** (i + 0.5) for i, p in enumerate(base.projections)) + mid.terminal_value_discounted
    assert mid.enterprise_value == pytest.approx(expected_ev)
    # Every flow is pulled forward half a year, so value rises by exactly (1 + wacc)^0.5.
    assert mid.enterprise_value == pytest.approx(base.enterprise_value * 1.1**0.5)
    assert mid.equity_value == pytest.approx(mid.enterprise_value - 200.0 + 100.0)


def test_mid_year_convention_exit_multiple_tv_stays_year_end():
    base = run_dcf(DcfInput(**_base_kwargs(), exit_multiple=8.0))
    mid = run_dcf(DcfInput(**_base_kwargs(), exit_multiple=8.0, mid_year_convention=True))

    assert mid.exit_multiple_terminal_value_undiscounted == pytest.approx(base.exit_multiple_terminal_value_undiscounted)
    assert mid.exit_multiple_terminal_value_discounted == pytest.approx(base.exit_multiple_terminal_value_discounted)
    pv_fcff_mid = sum(p.discounted_fcff for p in mid.projections)
    assert mid.exit_multiple_enterprise_value == pytest.approx(pv_fcff_mid + base.exit_multiple_terminal_value_discounted)


def test_exit_multiple_output_unchanged_by_discount_refactor_when_flag_off():
    r = run_dcf(DcfInput(**_base_kwargs(), exit_multiple=8.0))
    final = r.projections[-1]
    assert r.exit_multiple_terminal_value_discounted == pytest.approx(
        r.exit_multiple_terminal_value_undiscounted * final.discount_factor
    )
