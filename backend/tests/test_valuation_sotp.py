"""Unit tests for backend.valuation.sotp -- verifies the bridge arithmetic
including ownership adjustment, both corporate-overhead treatments, and the
conglomerate discount/upside calculation."""

from __future__ import annotations

import pytest

from backend.schemas.valuation import SotpInput, SotpSegmentInput
from backend.valuation.sotp import run_sotp


def _base_kwargs(treatment: str) -> dict:
    return dict(
        segments=[
            SotpSegmentInput(name="SegA", enterprise_value=1000.0, ownership_pct=1.0),
            SotpSegmentInput(name="SegB", enterprise_value=500.0, ownership_pct=0.6),
        ],
        cash_and_equivalents=200.0,
        marketable_securities=50.0,
        other_investments=0.0,
        total_debt=400.0,
        minority_interest=20.0,
        corporate_liabilities=30.0,
        corporate_overhead_annual=50.0,
        overhead_capitalization_multiple=5.0,
        corporate_overhead_treatment=treatment,
        diluted_shares_outstanding=100.0,
        current_share_price=10.0,
    )


def test_ownership_adjustment_and_segment_sum():
    inputs = SotpInput(**_base_kwargs("direct_deduction"))
    result = run_sotp(inputs)

    # SegA fully owned: attributed = 1000 * 1.0 = 1000
    # SegB 60% owned: attributed = 500 * 0.6 = 300
    assert result.segment_attributed_evs["SegA"] == pytest.approx(1000.0)
    assert result.segment_attributed_evs["SegB"] == pytest.approx(300.0)
    assert result.sum_of_segment_evs == pytest.approx(1300.0)


def test_direct_deduction_treatment():
    inputs = SotpInput(**_base_kwargs("direct_deduction"))
    result = run_sotp(inputs)

    # non-operating assets = 200 + 50 + 0 = 250
    # deductions = debt(400) + minority(20) + corp_liabilities(30) = 450
    # equity_direct = 1300 + 250 - 450 = 1100
    assert result.non_operating_assets == pytest.approx(250.0)
    assert result.equity_value_direct_deduction == pytest.approx(1100.0)
    assert result.equity_value == pytest.approx(1100.0)  # selected treatment
    assert result.implied_price_per_share == pytest.approx(11.0)


def test_capitalized_overhead_treatment():
    inputs = SotpInput(**_base_kwargs("capitalized_overhead"))
    result = run_sotp(inputs)

    # capitalized overhead deduction = 50 * 5 = 250
    # equity_capitalized = 1100 - 250 = 850
    assert result.capitalized_overhead_deduction == pytest.approx(250.0)
    assert result.equity_value_capitalized_overhead == pytest.approx(850.0)
    assert result.equity_value == pytest.approx(850.0)  # selected treatment
    assert result.implied_price_per_share == pytest.approx(8.5)

    # Both treatments' figures are always visible regardless of selection.
    assert result.equity_value_direct_deduction == pytest.approx(1100.0)


def test_conglomerate_discount_and_upside_direct_deduction():
    inputs = SotpInput(**_base_kwargs("direct_deduction"))
    result = run_sotp(inputs)

    # implied price = 11.0, current market price = 10.0
    # pct = (11 - 10) / 10 * 100 = 10%
    assert result.conglomerate_discount_pct == pytest.approx(10.0)
    assert result.upside_downside_pct == pytest.approx(10.0)


def test_conglomerate_discount_capitalized_overhead_is_negative():
    inputs = SotpInput(**_base_kwargs("capitalized_overhead"))
    result = run_sotp(inputs)

    # implied price = 8.5, current market price = 10.0
    # pct = (8.5 - 10) / 10 * 100 = -15%
    assert result.conglomerate_discount_pct == pytest.approx(-15.0)


def test_no_current_price_leaves_discount_fields_none():
    kwargs = _base_kwargs("direct_deduction")
    kwargs["current_share_price"] = None
    inputs = SotpInput(**kwargs)
    result = run_sotp(inputs)

    assert result.conglomerate_discount_pct is None
    assert result.upside_downside_pct is None
