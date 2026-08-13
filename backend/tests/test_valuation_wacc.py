"""Unit tests for backend.valuation.wacc -- verifies CAPM and WACC formulas
against a hand-calculated example."""

from __future__ import annotations

import pytest

from backend.schemas.valuation import WaccInput
from backend.valuation.wacc import compute_wacc


def test_wacc_matches_hand_calculation():
    inputs = WaccInput(
        risk_free_rate=0.04,
        beta=1.2,
        equity_risk_premium=0.05,
        cost_of_debt_pretax=0.06,
        tax_rate=0.25,
        market_value_equity=800.0,
        market_value_debt=200.0,
    )
    result = compute_wacc(inputs)

    # Hand calculation:
    # Ke = 0.04 + 1.2 * 0.05 = 0.10
    # Kd_after_tax = 0.06 * (1 - 0.25) = 0.045
    # E/(D+E) = 800/1000 = 0.8, D/(D+E) = 0.2
    # WACC = 0.10*0.8 + 0.045*0.2 = 0.08 + 0.009 = 0.089
    assert result.cost_of_equity == pytest.approx(0.10)
    assert result.cost_of_debt_after_tax == pytest.approx(0.045)
    assert result.equity_weight == pytest.approx(0.8)
    assert result.debt_weight == pytest.approx(0.2)
    assert result.wacc == pytest.approx(0.089)


def test_wacc_all_equity_financed():
    # With zero debt, WACC should equal cost of equity exactly.
    inputs = WaccInput(
        risk_free_rate=0.03,
        beta=1.0,
        equity_risk_premium=0.06,
        cost_of_debt_pretax=0.05,
        tax_rate=0.21,
        market_value_equity=500.0,
        market_value_debt=0.0,
    )
    result = compute_wacc(inputs)
    assert result.wacc == pytest.approx(result.cost_of_equity)
    assert result.wacc == pytest.approx(0.03 + 1.0 * 0.06)

