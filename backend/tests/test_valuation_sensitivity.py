"""Unit tests for backend.valuation.sensitivity -- verifies matrix shape and
that spot-check cells match direct recalculation via the underlying
valuation function."""

from __future__ import annotations

import pytest

from backend.schemas.valuation import DcfInput
from backend.valuation.dcf import run_dcf
from backend.valuation.sensitivity import dcf_sensitivity


def _base_dcf_kwargs() -> dict:
    return dict(
        base_revenue=1000.0,
        revenue_growth_rates=[0.10, 0.08],
        ebit_margins=[0.20, 0.20],
        tax_rate=0.25,
        da_pct_of_revenue=[0.05, 0.05],
        capex_pct_of_revenue=[0.06, 0.06],
        nwc_change_pct_of_revenue=[0.01, 0.01],
        wacc=0.10,  # will be overridden by the row grid
        terminal_growth_rate=0.03,  # will be overridden by the col grid
        net_debt=200.0,
        cash_and_equivalents=100.0,
        investments=0.0,
        minority_interest=0.0,
        diluted_shares_outstanding=100.0,
    )


def test_sensitivity_matrix_shape():
    row_values = [0.08, 0.09, 0.10]
    col_values = [0.02, 0.03]

    result = dcf_sensitivity(
        base_dcf_input_kwargs=_base_dcf_kwargs(),
        row_field="wacc",
        row_values=row_values,
        col_field="terminal_growth_rate",
        col_values=col_values,
    )

    assert result.row_values == row_values
    assert result.col_values == col_values
    assert len(result.matrix) == len(row_values)
    for row in result.matrix:
        assert len(row) == len(col_values)


def test_sensitivity_cell_matches_direct_recalculation():
    row_values = [0.08, 0.09, 0.10]
    col_values = [0.02, 0.03]
    base_kwargs = _base_dcf_kwargs()

    result = dcf_sensitivity(
        base_dcf_input_kwargs=base_kwargs,
        row_field="wacc",
        row_values=row_values,
        col_field="terminal_growth_rate",
        col_values=col_values,
    )

    # Spot-check the (wacc=0.09, g=0.03) cell (row index 1, col index 1)
    direct_kwargs = dict(base_kwargs)
    direct_kwargs["wacc"] = 0.09
    direct_kwargs["terminal_growth_rate"] = 0.03
    direct_result = run_dcf(DcfInput(**direct_kwargs))

    assert result.matrix[1][1] == pytest.approx(direct_result.implied_price_per_share)

    # And a second cell for good measure: (wacc=0.10, g=0.02) -> row 2, col 0
    direct_kwargs2 = dict(base_kwargs)
    direct_kwargs2["wacc"] = 0.10
    direct_kwargs2["terminal_growth_rate"] = 0.02
    direct_result2 = run_dcf(DcfInput(**direct_kwargs2))

    assert result.matrix[2][0] == pytest.approx(direct_result2.implied_price_per_share)
