"""Comparable company ("comps") analysis.

Pure functions, no I/O. Given an already-assembled peer set (peer selection
happens upstream, not here) and the target's own financials, computes
EV/Revenue, EV/EBITDA, EV/EBIT, P/E, P/B multiples per peer, summary
statistics (min/25th/median/75th/max, via numpy.percentile) across the peer
set, and the implied valuation from applying a single explicitly-chosen
multiple value (also chosen upstream -- this module does not pick the
multiple or the peer set) to the target's own metric.
"""

from __future__ import annotations

from typing import Optional

import numpy as np

from backend.schemas.valuation import (
    CompPeer,
    CompsInput,
    CompsResult,
    MultipleStats,
    PeerMultiples,
)

_METRICS = ["ev_to_revenue", "ev_to_ebitda", "ev_to_ebit", "price_to_earnings", "price_to_book"]


def _safe_div(numerator: float, denominator: float) -> Optional[float]:
    if denominator == 0:
        return None
    return numerator / denominator


def compute_peer_multiples(peer: CompPeer) -> PeerMultiples:
    ev = peer.enterprise_value
    return PeerMultiples(
        name=peer.name,
        ev_to_revenue=_safe_div(ev, peer.revenue),
        ev_to_ebitda=_safe_div(ev, peer.ebitda),
        ev_to_ebit=_safe_div(ev, peer.ebit),
        price_to_earnings=_safe_div(peer.market_cap, peer.net_income),
        price_to_book=_safe_div(peer.market_cap, peer.book_value_equity) if peer.book_value_equity is not None else None,
    )


def compute_multiple_stats(peer_multiples: list[PeerMultiples], metric: str) -> Optional[MultipleStats]:
    values = [getattr(pm, metric) for pm in peer_multiples if getattr(pm, metric) is not None]
    if not values:
        return None
    arr = np.array(values, dtype=float)
    return MultipleStats(
        metric=metric,  # type: ignore[arg-type]
        min=float(np.min(arr)),
        p25=float(np.percentile(arr, 25)),
        median=float(np.percentile(arr, 50)),
        p75=float(np.percentile(arr, 75)),
        max=float(np.max(arr)),
    )


def _target_metric_value(inputs: CompsInput, metric: str) -> Optional[float]:
    return {
        "ev_to_revenue": inputs.target_revenue,
        "ev_to_ebitda": inputs.target_ebitda,
        "ev_to_ebit": inputs.target_ebit,
        "price_to_earnings": inputs.target_net_income,
        "price_to_book": inputs.target_book_value_equity,
    }[metric]


def run_comps(inputs: CompsInput) -> CompsResult:
    peer_multiples = [compute_peer_multiples(p) for p in inputs.peers]
    stats = [s for s in (compute_multiple_stats(peer_multiples, m) for m in _METRICS) if s is not None]

    metric = inputs.chosen_multiple_metric
    multiple = inputs.chosen_multiple_value
    target_metric_value = _target_metric_value(inputs, metric)
    if target_metric_value is None:
        raise ValueError(f"Target does not provide a value for chosen metric '{metric}' (e.g. missing book value equity)")

    if metric in ("ev_to_revenue", "ev_to_ebitda", "ev_to_ebit"):
        implied_enterprise_value = target_metric_value * multiple
        implied_equity_value = implied_enterprise_value - inputs.target_debt + inputs.target_cash
    else:  # price_to_earnings / price_to_book apply directly to equity value
        implied_enterprise_value = None
        implied_equity_value = target_metric_value * multiple

    implied_price_per_share = implied_equity_value / inputs.target_diluted_shares_outstanding

    return CompsResult(
        inputs=inputs,
        peer_multiples=peer_multiples,
        stats=stats,
        implied_enterprise_value=implied_enterprise_value,
        implied_equity_value=implied_equity_value,
        implied_price_per_share=implied_price_per_share,
    )
