"""Comparable company ("comps") analysis.

Pure functions, no I/O. Given an already-assembled peer set (peer selection
happens upstream, not here) and the target's own financials, computes
EV/Revenue, EV/EBITDA, EV/EBIT, P/E, P/B multiples per peer, summary
statistics (min/25th/median/75th/max, via numpy.percentile) across the peer
set, and the implied valuation from applying a single explicitly-chosen
multiple value (also chosen upstream -- this module does not pick the
multiple or the peer set) to the target's own metric.

Nothing is dropped silently. A peer can be left out of ONE multiple's statistics
for two reasons, and each time it is reported in ``CompsResult.exclusions``:

* ``not_meaningful`` -- the multiple has no sensible value (non-positive or missing
  denominator). This is always applied.
* ``iqr_outlier_low`` / ``iqr_outlier_high`` -- only when ``trim_outliers`` is on:
  the multiple lies outside the fence [Q1 - k*IQR, Q3 + k*IQR] computed over that
  multiple's usable values (Q1/Q3 by ``numpy.percentile``, k = ``iqr_multiplier``,
  1.5 by default; the fence is widened by a relative 1e-9 so floating-point noise
  in identical values cannot create an "outlier"). Each multiple is trimmed independently, so a peer that is an
  outlier on P/E is still used for EV/EBITDA.

Trimming needs at least ``MIN_PEERS_FOR_TRIM`` usable values and is skipped, with a
warning, if it would leave fewer than ``MIN_PEERS_AFTER_TRIM``. It affects only the
reported statistics; it never changes ``chosen_multiple_value``, which stays an
explicit upstream choice.
"""

from __future__ import annotations

from typing import Optional

import numpy as np

from backend.schemas.valuation import (
    CompPeer,
    CompsInput,
    CompsResult,
    ModelWarning,
    MultipleStats,
    PeerExclusion,
    PeerMultiples,
)

_METRICS = ["ev_to_revenue", "ev_to_ebitda", "ev_to_ebit", "price_to_earnings", "price_to_book"]

MIN_PEERS_FOR_TRIM = 4  # an IQR needs a few points to mean anything
MIN_PEERS_AFTER_TRIM = 3  # never trim a peer set down to a handful
FEW_PEERS_WARN = 3  # fewer peers supplied than this draws a warning
FENCE_REL_TOL = 1e-9  # slack on the fences so float noise in an (almost) zero IQR cannot flag a peer


def _safe_div(numerator: float, denominator: float) -> Optional[float]:
    """A valuation multiple is only meaningful over a positive denominator.

    Negative EBITDA, EBIT or earnings (and negative book value) produce a
    negative "multiple" that says nothing about price relative to earnings, and
    would drag the min/median/percentiles of the peer set toward nonsense; zero
    is undefined. Both return None, so the peer drops out of that metric's
    statistics -- and is reported as a ``not_meaningful`` exclusion.
    """
    if denominator <= 0:
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


def _stats_from(metric: str, values: list[float], n_not_meaningful: int, n_outliers: int) -> MultipleStats:
    arr = np.array(values, dtype=float)
    return MultipleStats(
        metric=metric,  # type: ignore[arg-type]
        min=float(np.min(arr)),
        p25=float(np.percentile(arr, 25)),
        median=float(np.percentile(arr, 50)),
        p75=float(np.percentile(arr, 75)),
        max=float(np.max(arr)),
        n_peers_used=len(values),
        n_not_meaningful=n_not_meaningful,
        n_outliers_excluded=n_outliers,
    )


def analyse_multiple(
    peer_multiples: list[PeerMultiples],
    metric: str,
    *,
    trim_outliers: bool = False,
    iqr_multiplier: float = 1.5,
) -> tuple[Optional[MultipleStats], list[PeerExclusion], list[ModelWarning]]:
    """Statistics for one multiple, plus every exclusion and any warnings.

    Returns ``(stats, exclusions, warnings)``; ``stats`` is None when no peer has a
    usable value for this multiple.
    """
    exclusions: list[PeerExclusion] = []
    warnings: list[ModelWarning] = []
    usable: list[tuple[str, float]] = []
    for pm in peer_multiples:
        value = getattr(pm, metric)
        if value is None:
            exclusions.append(
                PeerExclusion(
                    peer=pm.name,
                    metric=metric,  # type: ignore[arg-type]
                    reason="not_meaningful",
                    value=None,
                    detail=f"{pm.name} has a non-positive or missing denominator, so {metric} is not meaningful.",
                )
            )
        else:
            usable.append((pm.name, value))
    n_not_meaningful = len(exclusions)

    outliers: list[PeerExclusion] = []
    if trim_outliers and usable:
        if len(usable) < MIN_PEERS_FOR_TRIM:
            warnings.append(
                ModelWarning(
                    code="TRIM_SKIPPED_TOO_FEW_PEERS",
                    message=(
                        f"Outlier trimming for {metric} was skipped: only {len(usable)} usable peers "
                        f"(need at least {MIN_PEERS_FOR_TRIM})."
                    ),
                )
            )
        else:
            values = np.array([v for _, v in usable], dtype=float)
            q1, q3 = (float(x) for x in np.percentile(values, [25, 75]))
            iqr = q3 - q1
            # When peers are identical the IQR is 0 and the fences collapse onto Q1/Q3; a relative
            # tolerance keeps a difference of one floating-point ulp from counting as an outlier.
            tol = FENCE_REL_TOL * max(1.0, abs(q1), abs(q3))
            lower, upper = q1 - iqr_multiplier * iqr - tol, q3 + iqr_multiplier * iqr + tol
            kept = [(n, v) for n, v in usable if lower <= v <= upper]
            if len(kept) < MIN_PEERS_AFTER_TRIM:
                warnings.append(
                    ModelWarning(
                        code="TRIM_SKIPPED_WOULD_LEAVE_TOO_FEW",
                        message=(
                            f"Outlier trimming for {metric} was skipped: it would leave {len(kept)} peers "
                            f"(minimum {MIN_PEERS_AFTER_TRIM}). All {len(usable)} usable peers were kept."
                        ),
                    )
                )
            else:
                for name, v in usable:
                    if v < lower or v > upper:
                        side = "low" if v < lower else "high"
                        outliers.append(
                            PeerExclusion(
                                peer=name,
                                metric=metric,  # type: ignore[arg-type]
                                reason=f"iqr_outlier_{side}",  # type: ignore[arg-type]
                                value=v,
                                lower_bound=lower,
                                upper_bound=upper,
                                detail=(
                                    f"{name}'s {metric} of {v:.2f} is outside the IQR fence "
                                    f"[{lower:.2f}, {upper:.2f}] (Q1={q1:.2f}, Q3={q3:.2f}, k={iqr_multiplier})."
                                ),
                            )
                        )
                usable = kept
    exclusions.extend(outliers)

    if not usable:
        return None, exclusions, warnings
    return _stats_from(metric, [v for _, v in usable], n_not_meaningful, len(outliers)), exclusions, warnings


def compute_multiple_stats(peer_multiples: list[PeerMultiples], metric: str) -> Optional[MultipleStats]:
    """Statistics over every peer with a meaningful value (no outlier trimming)."""
    return analyse_multiple(peer_multiples, metric)[0]


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
    stats: list[MultipleStats] = []
    exclusions: list[PeerExclusion] = []
    warnings: list[ModelWarning] = []
    for m in _METRICS:
        s, ex, w = analyse_multiple(
            peer_multiples, m, trim_outliers=inputs.trim_outliers, iqr_multiplier=inputs.iqr_multiplier
        )
        if s is not None:
            stats.append(s)
        exclusions.extend(ex)
        warnings.extend(w)

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

    if len(inputs.peers) < FEW_PEERS_WARN:
        warnings.append(
            ModelWarning(
                code="FEW_PEERS",
                message=f"Only {len(inputs.peers)} peer(s) supplied (warning below {FEW_PEERS_WARN}); the statistics are fragile.",
            )
        )
    chosen_stats = next((s for s in stats if s.metric == metric), None)
    if chosen_stats is None:
        warnings.append(
            ModelWarning(
                code="CHOSEN_METRIC_HAS_NO_PEER_STATS",
                message=f"No peer has a meaningful {metric}, so the chosen multiple ({multiple:.2f}) has nothing to be checked against.",
            )
        )
    elif not (chosen_stats.min <= multiple <= chosen_stats.max):
        warnings.append(
            ModelWarning(
                code="CHOSEN_MULTIPLE_OUTSIDE_PEER_RANGE",
                message=(
                    f"The chosen {metric} of {multiple:.2f} is outside the peer range "
                    f"[{chosen_stats.min:.2f}, {chosen_stats.max:.2f}]."
                ),
            )
        )

    outlier_peers = {e.peer for e in exclusions if e.reason.startswith("iqr_outlier")}
    return CompsResult(
        inputs=inputs,
        peer_multiples=peer_multiples,
        stats=stats,
        implied_enterprise_value=implied_enterprise_value,
        implied_equity_value=implied_equity_value,
        implied_price_per_share=implied_price_per_share,
        exclusions=exclusions,
        excluded_outlier_peer_count=len(outlier_peers),
        warnings=warnings,
    )
