"""WACC (weighted average cost of capital) computation.

Pure function, no I/O. CAPM cost of equity: Ke = rf + beta * ERP.
After-tax cost of debt: Kd_at = Kd_pretax * (1 - tax_rate).
WACC = Ke * E/(D+E) + Kd_at * D/(D+E).

All inputs (risk-free rate, equity risk premium, beta, cost of debt, tax
rate, capital weights) are caller-supplied -- this module hardcodes no market
assumptions.
"""

from __future__ import annotations

from backend.schemas.valuation import WaccInput, WaccResult


def compute_cost_of_equity(risk_free_rate: float, beta: float, equity_risk_premium: float) -> float:
    """CAPM: Ke = rf + beta * ERP."""
    return risk_free_rate + beta * equity_risk_premium


def compute_after_tax_cost_of_debt(cost_of_debt_pretax: float, tax_rate: float) -> float:
    return cost_of_debt_pretax * (1 - tax_rate)


def compute_wacc(inputs: WaccInput) -> WaccResult:
    cost_of_equity = compute_cost_of_equity(inputs.risk_free_rate, inputs.beta, inputs.equity_risk_premium)
    cost_of_debt_after_tax = compute_after_tax_cost_of_debt(inputs.cost_of_debt_pretax, inputs.tax_rate)

    total_capital = inputs.market_value_equity + inputs.market_value_debt
    if total_capital <= 0:
        raise ValueError("market_value_equity + market_value_debt must be positive")

    equity_weight = inputs.market_value_equity / total_capital
    debt_weight = inputs.market_value_debt / total_capital

    wacc = cost_of_equity * equity_weight + cost_of_debt_after_tax * debt_weight

    return WaccResult(
        inputs=inputs,
        cost_of_equity=cost_of_equity,
        cost_of_debt_after_tax=cost_of_debt_after_tax,
        equity_weight=equity_weight,
        debt_weight=debt_weight,
        wacc=wacc,
    )
