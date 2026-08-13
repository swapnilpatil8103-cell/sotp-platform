"""Discounted cash flow (DCF) valuation.

Pure functions, no I/O. Revenue is forecast from an explicit per-year growth
rate schedule (never invented). EBIT is derived from an explicit per-year
EBIT margin schedule. FCFF = NOPAT + D&A - CapEx - change in NWC. Terminal
value uses the Gordon Growth (perpetuity growth) formula:
TV = FCFF_(n+1) / (WACC - g). Both projected FCFF and TV are discounted to
present value at WACC to get enterprise value. EV -> equity value bridge:
equity value = EV - net_debt + cash + investments - minority_interest
(net_debt already nets out cash conceptually upstream, so cash is added back
here explicitly per the input contract -- see DcfInput field docs).
Equity value / diluted shares = implied price per share.
"""

from __future__ import annotations

from backend.schemas.valuation import DcfInput, DcfResult, DcfYearProjection


def _project_year(
    prior_revenue: float,
    growth_rate: float,
    ebit_margin: float,
    da_pct: float,
    capex_pct: float,
    nwc_change_pct: float,
    tax_rate: float,
    year_index: int,
) -> DcfYearProjection:
    revenue = prior_revenue * (1 + growth_rate)
    ebit = revenue * ebit_margin
    nopat = ebit * (1 - tax_rate)
    da = revenue * da_pct
    capex = revenue * capex_pct
    nwc_change = revenue * nwc_change_pct
    fcff = nopat + da - capex - nwc_change

    return DcfYearProjection(
        year_index=year_index,
        revenue=revenue,
        ebit=ebit,
        nopat=nopat,
        da=da,
        capex=capex,
        nwc_change=nwc_change,
        fcff=fcff,
        discount_factor=1.0,  # filled in by caller once wacc is known
        discounted_fcff=0.0,
    )


def compute_terminal_value(final_year_fcff: float, wacc: float, terminal_growth_rate: float) -> float:
    """Gordon Growth terminal value at the end of the explicit forecast
    horizon: TV = FCFF_(n+1) / (WACC - g), where FCFF_(n+1) = final_year_fcff
    * (1 + g)."""
    if wacc <= terminal_growth_rate:
        raise ValueError("wacc must exceed terminal_growth_rate for a finite Gordon Growth terminal value")
    fcff_next = final_year_fcff * (1 + terminal_growth_rate)
    return fcff_next / (wacc - terminal_growth_rate)


def run_dcf(inputs: DcfInput) -> DcfResult:
    inputs.validate_lengths()

    n_years = len(inputs.revenue_growth_rates)
    projections: list[DcfYearProjection] = []
    prior_revenue = inputs.base_revenue

    for i in range(n_years):
        year_index = i + 1
        proj = _project_year(
            prior_revenue=prior_revenue,
            growth_rate=inputs.revenue_growth_rates[i],
            ebit_margin=inputs.ebit_margins[i],
            da_pct=inputs.da_pct_of_revenue[i],
            capex_pct=inputs.capex_pct_of_revenue[i],
            nwc_change_pct=inputs.nwc_change_pct_of_revenue[i],
            tax_rate=inputs.tax_rate,
            year_index=year_index,
        )
        discount_factor = 1.0 / ((1 + inputs.wacc) ** year_index)
        proj.discount_factor = discount_factor
        proj.discounted_fcff = proj.fcff * discount_factor
        projections.append(proj)
        prior_revenue = proj.revenue

    final_fcff = projections[-1].fcff
    terminal_value_undiscounted = compute_terminal_value(final_fcff, inputs.wacc, inputs.terminal_growth_rate)
    final_discount_factor = projections[-1].discount_factor
    terminal_value_discounted = terminal_value_undiscounted * final_discount_factor

    enterprise_value = sum(p.discounted_fcff for p in projections) + terminal_value_discounted

    equity_value = (
        enterprise_value
        - inputs.net_debt
        + inputs.cash_and_equivalents
        + inputs.investments
        - inputs.minority_interest
    )
    implied_price_per_share = equity_value / inputs.diluted_shares_outstanding

    return DcfResult(
        inputs=inputs,
        projections=projections,
        terminal_value_undiscounted=terminal_value_undiscounted,
        terminal_value_discounted=terminal_value_discounted,
        enterprise_value=enterprise_value,
        equity_value=equity_value,
        implied_price_per_share=implied_price_per_share,
    )
