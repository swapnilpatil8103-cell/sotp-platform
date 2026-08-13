"""Value-unlock engine (deterministic math).

Given a current SOTP breakdown (``backend.valuation.sotp.SotpResult``, or
just its ``segment_attributed_evs``) and a proposed structural action, this
module computes current value vs. potential value vs. estimated uplift.
Every multiple/discount used is an explicit caller-supplied input -- nothing
here invents a "typical spin-off discount" or similar constant.

Supported action types and their explicit-input formulas:

- ``spin_off`` / ``segment_separation``: the segment currently sits inside
  the conglomerate at its attributed EV (which already reflects whatever
  conglomerate discount the market applies to the parent). Removing it and
  re-rating it standalone means: potential_value = segment's own
  standalone metric (e.g. EBITDA) * standalone_multiple (both explicit
  inputs). uplift = potential_value - current_segment_value.
- ``subsidiary_ipo``: same formula as spin-off, but caller may additionally
  supply ``ipo_discount_pct`` (a haircut applied to the standalone value to
  reflect a partial float / IPO pricing discount vs. a full spin-off).
  potential_value = standalone_metric * standalone_multiple * (1 - ipo_discount_pct).
- ``asset_sale``: potential_value = explicit ``sale_proceeds`` (a
  caller-supplied, already-negotiated-or-estimated number -- this module
  does not price the asset). uplift = sale_proceeds - current_segment_value.
- ``buyback``: no segment removed. uplift on a per-share basis:
  new_shares = shares_outstanding - (buyback_amount / current_share_price);
  new_equity_value = current_equity_value - buyback_amount (cash leaves the
  balance sheet); new_price = new_equity_value / new_shares.
  uplift_per_share = new_price - current_share_price.
- ``debt_reduction``: equity_value increases dollar-for-dollar as debt (and
  the associated deduction) falls: new_equity_value = current_equity_value +
  debt_reduced (cash used to pay down debt is assumed already reflected in
  ``cash_used``, i.e. equity value is unchanged unless caller separately
  supplies a re-rating; this module only computes the mechanical debt-paydown
  EV/equity bridge effect: equity unchanged if cash-funded pay-down, uplift
  only comes from an explicit ``new_leverage_multiple_delta`` re-rating input
  if supplied).
- ``special_dividend``: equity_value decreases by the dividend amount paid
  out (value leaves the company) but shareholders receive that amount
  directly -- net uplift to a holder is 0 by construction unless the caller
  supplies an explicit ``re_rating_multiple_delta`` reflecting a cleaner
  balance sheet being re-rated by the market.

Each action's compute function takes only explicit parameters; nothing is
defaulted to a "plausible" constant.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

ActionType = Literal[
    "spin_off",
    "subsidiary_ipo",
    "asset_sale",
    "buyback",
    "debt_reduction",
    "special_dividend",
    "segment_separation",
]


class ValueUnlockResult(BaseModel):
    action_type: ActionType
    current_value: float = Field(..., description="Value attributable to this action's subject before the action (segment EV, equity value, or share price, per action type)")
    potential_value: float = Field(..., description="Value attributable to this action's subject after the action")
    estimated_uplift: float = Field(..., description="potential_value - current_value")
    uplift_pct: Optional[float] = None
    detail: dict = Field(default_factory=dict)


def _pct(uplift: float, base: float) -> Optional[float]:
    if base == 0:
        return None
    return uplift / base * 100


def compute_spin_off_or_separation(
    *,
    action_type: Literal["spin_off", "segment_separation"],
    current_segment_value: float,
    standalone_metric_value: float,
    standalone_multiple: float,
) -> ValueUnlockResult:
    potential_value = standalone_metric_value * standalone_multiple
    uplift = potential_value - current_segment_value
    return ValueUnlockResult(
        action_type=action_type,
        current_value=current_segment_value,
        potential_value=potential_value,
        estimated_uplift=uplift,
        uplift_pct=_pct(uplift, current_segment_value),
        detail={"standalone_metric_value": standalone_metric_value, "standalone_multiple": standalone_multiple},
    )


def compute_subsidiary_ipo(
    *,
    current_segment_value: float,
    standalone_metric_value: float,
    standalone_multiple: float,
    ipo_discount_pct: float = 0.0,
) -> ValueUnlockResult:
    full_standalone_value = standalone_metric_value * standalone_multiple
    potential_value = full_standalone_value * (1 - ipo_discount_pct)
    uplift = potential_value - current_segment_value
    return ValueUnlockResult(
        action_type="subsidiary_ipo",
        current_value=current_segment_value,
        potential_value=potential_value,
        estimated_uplift=uplift,
        uplift_pct=_pct(uplift, current_segment_value),
        detail={
            "standalone_metric_value": standalone_metric_value,
            "standalone_multiple": standalone_multiple,
            "ipo_discount_pct": ipo_discount_pct,
            "full_standalone_value_pre_discount": full_standalone_value,
        },
    )


def compute_asset_sale(*, current_segment_value: float, sale_proceeds: float) -> ValueUnlockResult:
    uplift = sale_proceeds - current_segment_value
    return ValueUnlockResult(
        action_type="asset_sale",
        current_value=current_segment_value,
        potential_value=sale_proceeds,
        estimated_uplift=uplift,
        uplift_pct=_pct(uplift, current_segment_value),
        detail={"sale_proceeds": sale_proceeds},
    )


def compute_buyback(
    *,
    current_share_price: float,
    shares_outstanding: float,
    current_equity_value: float,
    buyback_amount: float,
) -> ValueUnlockResult:
    if current_share_price <= 0:
        raise ValueError("current_share_price must be positive")
    shares_repurchased = buyback_amount / current_share_price
    new_shares = shares_outstanding - shares_repurchased
    if new_shares <= 0:
        raise ValueError("buyback_amount implies repurchasing all or more than shares_outstanding")
    new_equity_value = current_equity_value - buyback_amount
    new_price = new_equity_value / new_shares
    uplift = new_price - current_share_price
    return ValueUnlockResult(
        action_type="buyback",
        current_value=current_share_price,
        potential_value=new_price,
        estimated_uplift=uplift,
        uplift_pct=_pct(uplift, current_share_price),
        detail={
            "shares_repurchased": shares_repurchased,
            "new_shares_outstanding": new_shares,
            "new_equity_value": new_equity_value,
        },
    )


def compute_debt_reduction(
    *,
    current_equity_value: float,
    debt_reduced: float,
    new_leverage_multiple_delta: float = 0.0,
) -> ValueUnlockResult:
    """Mechanical debt-paydown effect: a cash-funded paydown is
    equity-value-neutral (cash leaves, debt falls by the same amount) unless
    the caller supplies an explicit re-rating (``new_leverage_multiple_delta``,
    e.g. an EV/EBITDA point re-rating applied to EBITDA -- caller must pass
    the already-computed dollar delta here, this function does not invent a
    multiple)."""
    potential_value = current_equity_value + new_leverage_multiple_delta
    uplift = potential_value - current_equity_value
    return ValueUnlockResult(
        action_type="debt_reduction",
        current_value=current_equity_value,
        potential_value=potential_value,
        estimated_uplift=uplift,
        uplift_pct=_pct(uplift, current_equity_value),
        detail={"debt_reduced": debt_reduced, "new_leverage_multiple_delta": new_leverage_multiple_delta},
    )


def compute_special_dividend(
    *,
    current_equity_value: float,
    dividend_amount: float,
    re_rating_multiple_delta: float = 0.0,
) -> ValueUnlockResult:
    equity_after_payout = current_equity_value - dividend_amount
    potential_value = equity_after_payout + re_rating_multiple_delta
    # Net uplift to a holder = (potential_value + dividend received) - current_equity_value
    uplift = (potential_value + dividend_amount) - current_equity_value
    return ValueUnlockResult(
        action_type="special_dividend",
        current_value=current_equity_value,
        potential_value=potential_value,
        estimated_uplift=uplift,
        uplift_pct=_pct(uplift, current_equity_value),
        detail={
            "dividend_amount": dividend_amount,
            "equity_value_after_payout": equity_after_payout,
            "re_rating_multiple_delta": re_rating_multiple_delta,
        },
    )
