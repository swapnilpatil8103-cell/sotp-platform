"""Reverse valuation: solve for the single DCF assumption that reconciles a
DCF's implied enterprise value to a given market/target enterprise value,
holding every other assumption fixed, then classify how aggressive that
implied assumption is against explicit historical/peer ranges.

The solve itself is a deterministic numerical root-find
(``scipy.optimize.brentq``) over ``backend.valuation.dcf.run_dcf`` -- no AI,
no invented default range. It supports two solve targets:

- ``revenue_growth``: solves for a single flat YoY growth rate applied to
  every forecast year (replacing ``revenue_growth_rates``).
- ``terminal_growth``: solves for ``terminal_growth_rate``.

Classification (CONSERVATIVE / REASONABLE / AGGRESSIVE / EXTREME) is a
deterministic, quantile-based rule against an explicit historical range and
an explicit peer range (both required, caller-supplied -- never invented):

    combined_low  = min(historical_low, peer_low)
    combined_high = max(historical_high, peer_high)
    position = (implied - combined_low) / (combined_high - combined_low)

    position < 0.0               -> CONSERVATIVE (below both ranges' floor)
    0.0 <= position <= 1.0       -> REASONABLE (within the combined span)
    1.0 < position <= 1.5        -> AGGRESSIVE (above the span, within 50% of its width)
    position > 1.5               -> EXTREME

The same thresholds apply symmetrically below zero: position < -0.5 is also
EXTREME, -0.5 <= position < 0.0 is CONSERVATIVE. These exact cut points
(0.0, 1.0, 1.5, -0.5) are the documented, fixed thresholds for this
classification -- change them here (and in docs/valuation-methodology.md) if
the methodology is revised, not ad hoc at call sites.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field
from scipy.optimize import brentq

from backend.schemas.valuation import DcfInput
from backend.valuation.dcf import run_dcf

SolveTarget = Literal["revenue_growth", "terminal_growth"]
Classification = Literal["CONSERVATIVE", "REASONABLE", "AGGRESSIVE", "EXTREME"]


class RangeInput(BaseModel):
    low: float
    high: float


class ReverseValuationInput(BaseModel):
    base_dcf_input: DcfInput = Field(
        ..., description="DCF input with every assumption fixed EXCEPT the one being solved for"
    )
    target_enterprise_value: float = Field(..., description="Market-implied EV to reconcile against, e.g. market_cap + net_debt")
    solve_target: SolveTarget
    search_low: float = Field(..., description="Lower bound for the root-find search over the solved assumption")
    search_high: float = Field(..., description="Upper bound for the root-find search over the solved assumption")
    historical_range: RangeInput
    peer_range: RangeInput


class ReverseValuationResult(BaseModel):
    solve_target: SolveTarget
    implied_value: float
    target_enterprise_value: float
    achieved_enterprise_value: float
    historical_range: RangeInput
    peer_range: RangeInput
    position: float = Field(..., description="Position of implied_value within the combined historical/peer range; see module docstring for thresholds")
    classification: Classification


def _ev_for_revenue_growth(base_input: DcfInput, growth_rate: float) -> float:
    n = len(base_input.revenue_growth_rates)
    modified = base_input.model_copy(update={"revenue_growth_rates": [growth_rate] * n})
    return run_dcf(modified).enterprise_value


def _ev_for_terminal_growth(base_input: DcfInput, terminal_growth_rate: float) -> float:
    modified = base_input.model_copy(update={"terminal_growth_rate": terminal_growth_rate})
    return run_dcf(modified).enterprise_value


_SOLVERS = {
    "revenue_growth": _ev_for_revenue_growth,
    "terminal_growth": _ev_for_terminal_growth,
}


def classify_assumption(
    implied_value: float, historical_range: RangeInput, peer_range: RangeInput
) -> tuple[float, Classification]:
    """Deterministic, rule-based classification of ``implied_value`` against
    the combined span of ``historical_range`` and ``peer_range``. Returns
    (position, classification). See module docstring for exact thresholds.
    """
    combined_low = min(historical_range.low, peer_range.low)
    combined_high = max(historical_range.high, peer_range.high)
    width = combined_high - combined_low
    if width == 0:
        # Degenerate range: anything other than an exact match is EXTREME.
        position = 0.0 if implied_value == combined_low else float("inf")
        classification: Classification = "REASONABLE" if position == 0.0 else "EXTREME"
        return position, classification

    position = (implied_value - combined_low) / width

    if position < -0.5 or position > 1.5:
        classification = "EXTREME"
    elif position < 0.0:
        classification = "CONSERVATIVE"
    elif position <= 1.0:
        classification = "REASONABLE"
    else:
        classification = "AGGRESSIVE"

    return position, classification


def solve_reverse_valuation(inputs: ReverseValuationInput) -> ReverseValuationResult:
    """Root-find the assumption value (within [search_low, search_high]) that
    makes the DCF's enterprise value equal ``target_enterprise_value``, then
    classify it against the supplied historical/peer ranges.

    Raises ValueError if the target EV is not bracketed within
    [search_low, search_high] (i.e. brentq's sign-change precondition
    fails) -- this is a deliberate failure, not a fabricated answer.
    """
    ev_fn = _SOLVERS[inputs.solve_target]

    def objective(x: float) -> float:
        return ev_fn(inputs.base_dcf_input, x) - inputs.target_enterprise_value

    f_low = objective(inputs.search_low)
    f_high = objective(inputs.search_high)
    if f_low == 0:
        implied_value = inputs.search_low
    elif f_high == 0:
        implied_value = inputs.search_high
    elif (f_low > 0) == (f_high > 0):
        raise ValueError(
            f"target_enterprise_value ({inputs.target_enterprise_value}) is not bracketed by the DCF's "
            f"enterprise value over [{inputs.search_low}, {inputs.search_high}] "
            f"(EV at bounds: {f_low + inputs.target_enterprise_value}, {f_high + inputs.target_enterprise_value}). "
            "Widen search_low/search_high."
        )
    else:
        implied_value = brentq(objective, inputs.search_low, inputs.search_high, xtol=1e-10)

    achieved_ev = ev_fn(inputs.base_dcf_input, implied_value)
    position, classification = classify_assumption(implied_value, inputs.historical_range, inputs.peer_range)

    return ReverseValuationResult(
        solve_target=inputs.solve_target,
        implied_value=implied_value,
        target_enterprise_value=inputs.target_enterprise_value,
        achieved_enterprise_value=achieved_ev,
        historical_range=inputs.historical_range,
        peer_range=inputs.peer_range,
        position=position,
        classification=classification,
    )
