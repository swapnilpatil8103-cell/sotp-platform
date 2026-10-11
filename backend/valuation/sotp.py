"""Sum-of-the-parts (SOTP) valuation bridge.

Pure function, no I/O. Accepts pre-computed segment enterprise values (from
DCF or comps, computed upstream) each with an explicit ownership_pct, sums
them (ownership-adjusted), adds non-operating assets (cash, marketable
securities, other investments), and deducts debt, minority interest,
corporate liabilities, and -- depending on the explicit
``corporate_overhead_treatment`` -- a capitalized corporate overhead
liability. Both treatments' equity values are always computed and returned
so the caller can see both regardless of which one is "selected".

    equity_value = sum(segment_ev * ownership_pct)
                 + cash + marketable_securities + other_investments
                 - total_debt - minority_interest - corporate_liabilities
                 [- corporate_overhead_annual * overhead_capitalization_multiple]  (capitalized_overhead only)

Also computes, against an optional current market share price (pure
arithmetic, two different denominators on purpose):

    upside_downside_pct      = (implied - market) / market  * 100
    conglomerate_discount_pct = (implied - market) / implied * 100

upside/downside answers "how far could the stock move to reach SOTP value"
(base = market price). The conglomerate discount is the market's discount to
the sum of the parts (base = SOTP-implied price), the conventional definition.
Sign: POSITIVE = market trades below SOTP value (a discount); NEGATIVE = market
trades above it (a premium). It is None when there is no market price or when
the implied price is not positive (a discount to a non-positive value is
undefined). Worked example: implied 11, market 10 -> upside +10.0%, discount
+9.09%; implied 8.5, market 10 -> upside -15.0%, discount -17.65% (premium).
"""

from __future__ import annotations

from backend.schemas.valuation import ModelWarning, SotpInput, SotpResult

LARGE_GAP_TO_MARKET_PCT = 50.0  # |upside/downside| above this draws a warning


def run_sotp(inputs: SotpInput) -> SotpResult:
    segment_attributed_evs = {seg.name: seg.attributed_ev for seg in inputs.segments}
    sum_of_segment_evs = sum(segment_attributed_evs.values())

    non_operating_assets = inputs.cash_and_equivalents + inputs.marketable_securities + inputs.other_investments

    base_deductions = inputs.total_debt + inputs.minority_interest + inputs.corporate_liabilities

    equity_value_direct_deduction = sum_of_segment_evs + non_operating_assets - base_deductions

    capitalized_overhead_deduction = inputs.corporate_overhead_annual * inputs.overhead_capitalization_multiple
    equity_value_capitalized_overhead = equity_value_direct_deduction - capitalized_overhead_deduction

    if inputs.corporate_overhead_treatment == "direct_deduction":
        equity_value = equity_value_direct_deduction
    else:
        equity_value = equity_value_capitalized_overhead

    implied_price_per_share = equity_value / inputs.diluted_shares_outstanding

    conglomerate_discount_pct = None
    upside_downside_pct = None
    if inputs.current_share_price:
        gap = implied_price_per_share - inputs.current_share_price
        upside_downside_pct = gap / inputs.current_share_price * 100
        if implied_price_per_share > 0:
            conglomerate_discount_pct = gap / implied_price_per_share * 100

    result = SotpResult(
        inputs=inputs,
        segment_attributed_evs=segment_attributed_evs,
        sum_of_segment_evs=sum_of_segment_evs,
        non_operating_assets=non_operating_assets,
        capitalized_overhead_deduction=capitalized_overhead_deduction,
        equity_value_direct_deduction=equity_value_direct_deduction,
        equity_value_capitalized_overhead=equity_value_capitalized_overhead,
        equity_value=equity_value,
        implied_price_per_share=implied_price_per_share,
        conglomerate_discount_pct=conglomerate_discount_pct,
        upside_downside_pct=upside_downside_pct,
    )
    result.warnings = sotp_warnings(inputs, result)
    return result


def sotp_warnings(inputs: SotpInput, result: SotpResult) -> list[ModelWarning]:
    """Non-fatal observations about a computed SOTP. They never change a figure."""
    out: list[ModelWarning] = []

    if result.equity_value < 0:
        out.append(
            ModelWarning(
                code="NEGATIVE_EQUITY_VALUE",
                message=f"Equity value is negative ({result.equity_value:,.0f}); deductions exceed the sum of the parts plus non-operating assets.",
            )
        )

    negative = [name for name, ev in result.segment_attributed_evs.items() if ev < 0]
    if negative:
        out.append(
            ModelWarning(
                code="NEGATIVE_SEGMENT_EV",
                message=f"Segment(s) with a negative attributed enterprise value: {', '.join(negative)}. Check the upstream segment valuation.",
            )
        )

    if inputs.corporate_overhead_treatment == "direct_deduction" and inputs.corporate_overhead_annual > 0:
        out.append(
            ModelWarning(
                code="OVERHEAD_NOT_DEDUCTED",
                message=(
                    f"Annual corporate overhead of {inputs.corporate_overhead_annual:,.0f} is entered but the "
                    f"direct_deduction treatment does not deduct it; capitalising it would reduce equity value by "
                    f"{result.capitalized_overhead_deduction:,.0f} (see equity_value_capitalized_overhead)."
                ),
            )
        )

    gap = result.upside_downside_pct
    if gap is not None and abs(gap) > LARGE_GAP_TO_MARKET_PCT:
        direction = "above" if gap > 0 else "below"
        out.append(
            ModelWarning(
                code="LARGE_GAP_TO_MARKET",
                message=(
                    f"The SOTP-implied price is {abs(gap):.1f}% {direction} the current market price "
                    f"(warning beyond {LARGE_GAP_TO_MARKET_PCT:.0f}%); re-check the segment values and deductions."
                ),
            )
        )
    return out
