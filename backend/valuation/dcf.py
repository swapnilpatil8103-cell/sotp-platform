"""Discounted cash flow (DCF) valuation.

Pure functions, no I/O. Revenue is forecast from an explicit per-year growth
rate schedule (never invented). EBIT is derived from an explicit per-year
EBIT margin schedule. FCFF = NOPAT + D&A - CapEx - change in NWC. Terminal
value uses the Gordon Growth (perpetuity growth) formula:
TV = FCFF_(n+1) / (WACC - g). Both projected FCFF and TV are discounted to
present value at WACC to get enterprise value. EV -> equity value bridge:
equity value = EV - total_debt + cash + investments - minority_interest,
where total_debt is GROSS debt (not net of cash -- cash is added back exactly
once, via cash_and_equivalents). This matches SotpInput's bridge.
Equity value / diluted shares = implied price per share.

Net working capital investment is, by default, a per-year percentage of that year's
revenue (``nwc_change_pct_of_revenue``). Alternatively ``nwc_pct_of_incremental_revenue``
sets it to a fraction of the year-over-year INCREASE in revenue (year 1 against
``base_revenue``). In that mode the Gordon terminal value uses a steady-state NWC
investment, rate x revenue_n x g, instead of the last explicit year's, because the
last explicit year's investment reflects that year's growth, not the perpetuity's.

Non-fatal warnings (``DcfResult.warnings``) flag assumptions an analyst should look at:
a negative terminal cash flow, a terminal value above 85% of enterprise value, terminal
growth above a supplied risk-free rate, WACC below 5%, a WACC-g spread under 2 points,
and a negative equity value. They never change a number.

Discounting is end-of-year (period t) by default. With
``mid_year_convention=True`` the year-t FCFF is discounted at t - 0.5 and the
Gordon Growth terminal value at N - 0.5 (it is a perpetuity of mid-year flows);
the Exit Multiple terminal value, a year-end sale price, stays at N.
"""

from __future__ import annotations

import math
from typing import Optional

from backend.schemas.valuation import DcfInput, DcfResult, DcfYearProjection, ModelWarning

# Thresholds for the non-fatal warnings. Warnings never alter any figure.
TERMINAL_VALUE_SHARE_WARN = 0.85  # discounted TV / EV above this
LOW_WACC_WARN = 0.05  # WACC below this
THIN_SPREAD_WARN = 0.02  # WACC - terminal growth below this


def _project_year(
    prior_revenue: float,
    growth_rate: float,
    ebit_margin: float,
    da_pct: float,
    capex_pct: float,
    nwc_change_pct: Optional[float],
    nwc_pct_of_incremental: Optional[float],
    tax_rate: float,
    year_index: int,
) -> DcfYearProjection:
    revenue = prior_revenue * (1 + growth_rate)
    ebit = revenue * ebit_margin
    nopat = ebit * (1 - tax_rate)
    da = revenue * da_pct
    capex = revenue * capex_pct
    if nwc_pct_of_incremental is not None:
        nwc_change = nwc_pct_of_incremental * (revenue - prior_revenue)
    else:
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
    """Run the DCF. Raises ``ValueError`` for any input the model cannot
    compute, including arithmetic failures (``ZeroDivisionError`` /
    ``OverflowError``): every caller already maps ``ValueError`` to a 400, so
    converting here fixes them all at once instead of each endpoint needing its
    own ``except``. ``DcfInput`` bounds ``wacc > -1``, but a discount factor can
    still underflow to zero (wacc just above -1 over a long horizon) or
    overflow (a very large wacc), and the schema cannot rule those out.
    """
    try:
        result = _run_dcf(inputs)
    except ArithmeticError as exc:
        raise ValueError(
            f"DCF could not be computed for these inputs ({type(exc).__name__}: {exc}); "
            "check that wacc is greater than -1 and not so extreme that discount factors "
            "underflow or overflow over the forecast horizon."
        ) from exc

    # Float multiplication overflows to inf silently (only ** and / raise), which
    # would otherwise be serialised as nulls on a 200.
    for name in ("enterprise_value", "equity_value", "implied_price_per_share"):
        if not math.isfinite(getattr(result, name)):
            raise ValueError(f"DCF produced a non-finite {name}; the inputs are too large to compute.")
    return result


def _run_dcf(inputs: DcfInput) -> DcfResult:
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
            nwc_change_pct=inputs.nwc_change_pct_of_revenue[i] if inputs.nwc_change_pct_of_revenue is not None else None,
            nwc_pct_of_incremental=inputs.nwc_pct_of_incremental_revenue,
            tax_rate=inputs.tax_rate,
            year_index=year_index,
        )
        discount_period = year_index - 0.5 if inputs.mid_year_convention else year_index
        discount_factor = 1.0 / ((1 + inputs.wacc) ** discount_period)
        proj.discount_factor = discount_factor
        proj.discounted_fcff = proj.fcff * discount_factor
        projections.append(proj)
        prior_revenue = proj.revenue

    final = projections[-1]
    g = inputs.terminal_growth_rate
    final_fcff = final.fcff
    if inputs.nwc_pct_of_incremental_revenue is not None:
        # Steady state: from year n+1 revenue grows at g, so NWC investment is rate x Rev_n x g.
        # compute_terminal_value multiplies by (1+g), so hand it the equivalent year-n figure:
        # FCFF_(n+1) = (FCFF_n + NWC_n)(1+g) - rate x Rev_n x g.
        pre_nwc_fcff = final.fcff + final.nwc_change
        final_fcff = pre_nwc_fcff - inputs.nwc_pct_of_incremental_revenue * final.revenue * g / (1 + g)
    terminal_value_undiscounted = compute_terminal_value(final_fcff, inputs.wacc, g)
    terminal_fcff = final_fcff * (1 + g)
    final_discount_factor = projections[-1].discount_factor
    terminal_value_discounted = terminal_value_undiscounted * final_discount_factor

    sum_pv_fcff = sum(p.discounted_fcff for p in projections)
    enterprise_value = sum_pv_fcff + terminal_value_discounted

    equity_value = (
        enterprise_value
        - inputs.total_debt
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
        # Year-end sale price: always discounted a full N periods, even under the
        # mid-year convention (final_year.discount_factor would be N - 0.5).
        exit_tv_discounted = exit_tv_undiscounted / ((1 + inputs.wacc) ** n_years)
        exit_ev = sum_pv_fcff + exit_tv_discounted
        exit_equity = (
            exit_ev
            - inputs.total_debt
            + inputs.cash_and_equivalents
            + inputs.investments
            - inputs.minority_interest
        )
        exit_price = exit_equity / inputs.diluted_shares_outstanding

    result = DcfResult(
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
        terminal_fcff=terminal_fcff,
        terminal_value_share_of_ev=(terminal_value_discounted / enterprise_value) if enterprise_value > 0 else None,
    )
    result.warnings = dcf_warnings(inputs, result)
    return result


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def dcf_warnings(inputs: DcfInput, result: DcfResult) -> list[ModelWarning]:
    """Non-fatal observations about a computed DCF. Pure function of the inputs and result."""
    out: list[ModelWarning] = []
    g, wacc = inputs.terminal_growth_rate, inputs.wacc

    if result.terminal_fcff is not None and result.terminal_fcff < 0:
        out.append(
            ModelWarning(
                code="NEGATIVE_TERMINAL_FCFF",
                message=(
                    f"The terminal-year free cash flow (FCFF_n+1 = {result.terminal_fcff:,.0f}) is negative, so "
                    "the Gordon Growth terminal value is negative and subtracts value. Check the final-year "
                    "margins, CapEx and working-capital assumptions."
                ),
            )
        )

    share = result.terminal_value_share_of_ev
    if share is not None and share > TERMINAL_VALUE_SHARE_WARN:
        out.append(
            ModelWarning(
                code="TERMINAL_VALUE_SHARE_HIGH",
                message=(
                    f"The discounted terminal value is {_pct(share)} of enterprise value (warning above "
                    f"{_pct(TERMINAL_VALUE_SHARE_WARN)}): the valuation rests almost entirely on the "
                    "perpetuity assumptions rather than the explicit forecast."
                ),
            )
        )
    exit_ev = result.exit_multiple_enterprise_value
    if exit_ev is not None and exit_ev > 0 and result.exit_multiple_terminal_value_discounted is not None:
        exit_share = result.exit_multiple_terminal_value_discounted / exit_ev
        if exit_share > TERMINAL_VALUE_SHARE_WARN:
            out.append(
                ModelWarning(
                    code="EXIT_MULTIPLE_TERMINAL_VALUE_SHARE_HIGH",
                    message=(
                        f"The discounted exit-multiple terminal value is {_pct(exit_share)} of the exit-multiple "
                        f"enterprise value (warning above {_pct(TERMINAL_VALUE_SHARE_WARN)})."
                    ),
                )
            )

    if inputs.risk_free_rate is not None and g > inputs.risk_free_rate:
        out.append(
            ModelWarning(
                code="TERMINAL_GROWTH_ABOVE_RISK_FREE",
                message=(
                    f"Terminal growth ({_pct(g)}) exceeds the risk-free rate ({_pct(inputs.risk_free_rate)}). A "
                    "company growing faster than the risk-free rate forever would eventually outgrow the economy."
                ),
            )
        )
    if wacc < LOW_WACC_WARN:
        out.append(
            ModelWarning(
                code="LOW_WACC",
                message=f"WACC ({_pct(wacc)}) is below {_pct(LOW_WACC_WARN)}, which is unusually low and inflates present values.",
            )
        )
    spread = wacc - g
    if 0 < spread < THIN_SPREAD_WARN:
        out.append(
            ModelWarning(
                code="THIN_WACC_SPREAD",
                message=(
                    f"WACC minus terminal growth is only {_pct(spread)} (warning below {_pct(THIN_SPREAD_WARN)}); "
                    "the terminal value is extremely sensitive to small changes in either."
                ),
            )
        )
    if result.equity_value < 0:
        out.append(
            ModelWarning(
                code="NEGATIVE_EQUITY_VALUE",
                message=f"Equity value is negative ({result.equity_value:,.0f}); debt and other claims exceed the enterprise value.",
            )
        )
    return out
