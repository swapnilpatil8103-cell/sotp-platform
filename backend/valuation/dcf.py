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


def compute_exit_multiple_terminal_value(terminal_year_ebitda: float, exit_multiple: float) -> float:
    """Exit Multiple terminal value: TV = terminal_year_EBITDA * exit_multiple.

    Independent of, and never a substitute for, the Gordon Growth terminal
    value above -- both are computed and returned side by side when
    ``exit_multiple`` is supplied on ``DcfInput``. ``exit_multiple`` is always
    an explicit caller-supplied input; nothing here invents or defaults it.
    """
    return terminal_year_ebitda * exit_multiple


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

    sum_pv_fcff = sum(p.discounted_fcff for p in projections)
    enterprise_value = sum_pv_fcff + terminal_value_discounted

    equity_value = (
        enterprise_value
        - inputs.net_debt
        + inputs.cash_and_equivalents
        + inputs.investments
        - inputs.minority_interest
    )
    implied_price_per_share = equity_value / inputs.diluted_shares_outstanding

    # -- Optional, independent Exit Multiple terminal value (additive-only:
    # never touches Gordon Growth fields above). Only computed when the
    # caller explicitly supplies exit_multiple.
    terminal_year_ebitda = None
    exit_tv_undiscounted = None
    exit_tv_discounted = None
    exit_ev = None
    exit_equity = None
    exit_price = None
    if inputs.exit_multiple is not None:
        final_year = projections[-1]
        terminal_year_ebitda = final_year.ebit + final_year.da
        exit_tv_undiscounted = compute_exit_multiple_terminal_value(terminal_year_ebitda, inputs.exit_multiple)
        exit_tv_discounted = exit_tv_undiscounted * final_year.discount_factor
        exit_ev = sum_pv_fcff + exit_tv_discounted
        exit_equity = (
            exit_ev
            - inputs.net_debt
            + inputs.cash_and_equivalents
            + inputs.investments
            - inputs.minority_interest
        )
        exit_price = exit_equity / inputs.diluted_shares_outstanding

    return DcfResult(
        inputs=inputs,
        projections=projections,
        terminal_value_undiscounted=terminal_value_undiscounted,
        terminal_value_discounted=terminal_value_discounted,
        enterprise_value=enterprise_value,
        equity_value=equity_value,
        implied_price_per_share=implied_price_per_share,
        terminal_year_ebitda=terminal_year_ebitda,
        exit_multiple_terminal_value_undiscounted=exit_tv_undiscounted,
        exit_multiple_terminal_value_discounted=exit_tv_discounted,
        exit_multiple_enterprise_value=exit_ev,
        exit_multiple_equity_value=exit_equity,
        exit_multiple_implied_price_per_share=exit_price,
    )
