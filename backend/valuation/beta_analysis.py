"""Peer-informed beta unlevering / relevering (Hamada equation).

Pure functions, no I/O, no AI. Given a set of peers' levered ("raw") betas
and their capital structures (D/E) and tax rates, unlever each peer's beta to
strip out its own leverage effect, aggregate the resulting unlevered betas
(median or average, caller's choice) across the peer set, then relever that
aggregate at the *target* company's own D/E and tax rate to produce a
peer-informed levered beta suggestion for the target.

    beta_unlevered_peer = beta_levered_peer / (1 + (1 - tax_rate) * (D/E))
    beta_unlevered_aggregate = median(...) or average(...) across peers
    beta_levered_target = beta_unlevered_aggregate * (1 + (1 - tax_rate_target) * (D/E)_target)

Every peer's D/E and tax rate must be real (caller-supplied, or sourced
upstream from market-data/peer_discovery pipelines) -- a peer missing either
figure is skipped and reported, never fabricated or defaulted to 0 or 1.
This module never fetches data itself; callers assemble ``PeerBetaInput``
rows from whatever real-data pipeline they used (market_data_client.py for
beta, peer_discovery.py / real financials for D/E and tax rate) before
calling in here.

This is a *suggestion* generator only. Nothing here writes a WACC input,
AssumptionDecision, or ValuationRun -- callers/routers must route the result
through the existing propose/approve governance flow (see
backend/governance/approval.py) before it can feed a persisted valuation.
"""

from __future__ import annotations

import statistics
from typing import Literal, Optional

from pydantic import BaseModel, Field


class PeerBetaInput(BaseModel):
    name: str
    levered_beta: float = Field(..., description="Peer's raw/levered (equity) beta, e.g. from market data")
    debt_to_equity: Optional[float] = Field(
        None, description="Peer's real D/E (market value of debt / market value of equity). None => peer is skipped, never assumed 0."
    )
    tax_rate: Optional[float] = Field(
        None, description="Peer's real effective/marginal tax rate. None => peer is skipped, never assumed 0."
    )


class SkippedPeer(BaseModel):
    name: str
    reason: str


class PeerUnleveredBeta(BaseModel):
    name: str
    levered_beta: float
    debt_to_equity: float
    tax_rate: float
    unlevered_beta: float


class BetaAnalysisInput(BaseModel):
    peers: list[PeerBetaInput] = Field(..., min_length=1)
    aggregation: Literal["median", "average"] = Field("median")
    target_debt_to_equity: float = Field(..., ge=0, description="Target company's own D/E, used to relever")
    target_tax_rate: float = Field(..., description="Target company's own effective/marginal tax rate, used to relever")


class BetaAnalysisResult(BaseModel):
    used_peers: list[PeerUnleveredBeta]
    skipped_peers: list[SkippedPeer]
    aggregation: Literal["median", "average"]
    unlevered_beta_aggregate: float
    target_debt_to_equity: float
    target_tax_rate: float
    relevered_beta: float = Field(..., description="Peer-informed suggested levered beta for the target -- SUGGESTED, requires human review before use in a WACC/ValuationRun")


def unlever_beta(levered_beta: float, debt_to_equity: float, tax_rate: float) -> float:
    """beta_u = beta_l / (1 + (1 - T) * (D/E))"""
    return levered_beta / (1 + (1 - tax_rate) * debt_to_equity)


def relever_beta(unlevered_beta: float, debt_to_equity: float, tax_rate: float) -> float:
    """beta_l = beta_u * (1 + (1 - T) * (D/E))"""
    return unlevered_beta * (1 + (1 - tax_rate) * debt_to_equity)


def run_beta_analysis(inputs: BetaAnalysisInput) -> BetaAnalysisResult:
    used: list[PeerUnleveredBeta] = []
    skipped: list[SkippedPeer] = []

    for peer in inputs.peers:
        if peer.debt_to_equity is None or peer.tax_rate is None:
            missing = []
            if peer.debt_to_equity is None:
                missing.append("debt_to_equity")
            if peer.tax_rate is None:
                missing.append("tax_rate")
            skipped.append(
                SkippedPeer(
                    name=peer.name,
                    reason=f"Missing real capital-structure data: {', '.join(missing)} -- skipped rather than fabricated.",
                )
            )
            continue

        beta_u = unlever_beta(peer.levered_beta, peer.debt_to_equity, peer.tax_rate)
        used.append(
            PeerUnleveredBeta(
                name=peer.name,
                levered_beta=peer.levered_beta,
                debt_to_equity=peer.debt_to_equity,
                tax_rate=peer.tax_rate,
                unlevered_beta=beta_u,
            )
        )

    if not used:
        raise ValueError("No peer had both debt_to_equity and tax_rate available -- cannot compute a peer-informed beta.")

    unlevered_values = [p.unlevered_beta for p in used]
    aggregate = (
        statistics.median(unlevered_values) if inputs.aggregation == "median" else statistics.fmean(unlevered_values)
    )

    relevered = relever_beta(aggregate, inputs.target_debt_to_equity, inputs.target_tax_rate)

    return BetaAnalysisResult(
        used_peers=used,
        skipped_peers=skipped,
        aggregation=inputs.aggregation,
        unlevered_beta_aggregate=aggregate,
        target_debt_to_equity=inputs.target_debt_to_equity,
        target_tax_rate=inputs.target_tax_rate,
        relevered_beta=relevered,
    )
