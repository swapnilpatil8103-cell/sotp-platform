"""Peer discovery via the real SEC XBRL "frames" API.

Given a target company (CIK/ticker) and a fiscal period, discover candidate
comps peers by pulling a single concept's frame (all filers who reported that
concept for the period), narrowing the (potentially thousands-wide) frame down
to a small shortlist of candidate CIKs, then cross-referencing each
shortlisted CIK's cached SEC submissions to confirm it shares the target's
business classification (SIC-code bucket, via
``backend.data.business_classifier``).

This module is a *discovery* tool, not a peer-selection or comps-execution
tool:
  - It never calls backend.valuation.comps.run_comps.
  - It never auto-selects/finalizes which peers get used in a comps run.
  - Every returned candidate is transparently sourced (CIK, ticker, name, the
    frame concept value, and best-effort real financial facts pulled via the
    existing concept_mapping/normalizer machinery) so a human (or an explicit
    caller action) can choose to promote some subset of candidates into a
    CompPeer set -- that promotion step lives elsewhere, deliberately outside
    this module, per the project's human-approval-gate convention (the same
    convention AI peer recommendation in backend/ai/tasks/peer_recommendation.py
    follows: propose, never auto-apply).

No fabrication: if a candidate is missing a needed financial concept for the
period, it comes back flagged MISSING (via the normalizer's existing
REPORTED/MISSING discipline) rather than estimated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from backend.data.business_classifier import BusinessClassification, classify_business
from backend.data.concept_mapping import resolve_concept
from backend.data.historical_analysis import (
    available_fiscal_years,
    compute_cagr,
    normalize_company_facts_multi_year,
)
from backend.data.normalizer import _pick_fact_for_period
from backend.services.sec_client import SECConnector, SECError

# Concept used to define the frame -- i.e. which companies to consider at all.
# Revenue is the most universally-reported duration concept across industries.
DEFAULT_FRAME_CONCEPT_TAG = "Revenues"

# Financial concepts pulled per shortlisted candidate so the shortlist can
# populate a CompPeer if a caller chooses to. Deliberately mirrors what
# CompPeer needs (backend/schemas/valuation.py): revenue, ebitda components
# (via da/operating_income), ebit, net_income, debt, cash. market_cap/EV
# inputs (share price) are NOT available from XBRL frames/facts (that's
# market data, out of scope for this module -- see MarketDataClient).
PEER_FINANCIAL_CONCEPTS = [
    "revenue",
    "operating_income",
    "da",
    "net_income",
    "total_debt",
    "cash",
]


@dataclass
class CandidateFinancialFact:
    concept: str
    value: Optional[float]
    unit: Optional[str]
    xbrl_tag: Optional[str]
    data_status: str  # "REPORTED" | "MISSING"


@dataclass
class SimilarityScore:
    """Composite peer-similarity score: revenue-proximity + margin + growth.

    Never fabricated -- each dimension is either computed from real REPORTED
    facts or comes back None with a caveat explaining why (missing data),
    in which case that dimension is excluded from the weighted average and
    the candidate's `data_completeness` drops, pushing it lower in the
    ranking rather than crediting it with fabricated similarity.
    """

    revenue_proximity_score: Optional[float]  # 0..1, 1 = identical revenue
    operating_margin_pct: Optional[float]
    revenue_cagr_pct: Optional[float]
    margin_similarity_score: Optional[float]  # 0..1, 1 = identical margin to target
    growth_similarity_score: Optional[float]  # 0..1, 1 = identical CAGR to target
    data_completeness: float  # fraction of the 3 dimensions actually available (0..1)
    composite_score: float  # 0..1, higher = better comp; penalized by data_completeness
    caveats: list[str] = field(default_factory=list)


@dataclass
class PeerCandidate:
    cik10: str
    ticker: Optional[str]
    entity_name: str
    sic: Optional[str]
    sic_description: Optional[str]
    classification: BusinessClassification
    frame_concept: str
    frame_value: float
    financials: list[CandidateFinancialFact] = field(default_factory=list)
    similarity: Optional[SimilarityScore] = None


@dataclass
class PeerDiscoveryResult:
    target_cik10: str
    target_classification: BusinessClassification
    frame_concept: str
    fiscal_year: int
    quarter: Optional[int]
    frame_company_count: int
    candidates: list[PeerCandidate] = field(default_factory=list)
    note: Optional[str] = None


def _ticker_for_cik(cik10: str, ticker_map: dict[str, str]) -> Optional[str]:
    # ticker_map is ticker(upper) -> cik10; invert on demand (small shortlist only).
    for ticker, cik in ticker_map.items():
        if cik == cik10:
            return ticker
    return None


def _fetch_candidate_financials(client: SECConnector, cik10: str) -> list[CandidateFinancialFact]:
    """Pull real reported values for PEER_FINANCIAL_CONCEPTS for this CIK's most
    recent available FY, using the existing concept_mapping/normalizer machinery
    (no duplication of tag-resolution logic). MISSING (never fabricated) when a
    concept isn't found."""
    try:
        company_facts = client.get_company_facts(cik10)
    except SECError:
        return [
            CandidateFinancialFact(concept=c, value=None, unit=None, xbrl_tag=None, data_status="MISSING")
            for c in PEER_FINANCIAL_CONCEPTS
        ]

    from backend.data.normalizer import latest_available_fiscal_year

    fy = latest_available_fiscal_year(company_facts, fiscal_period="FY")

    results: list[CandidateFinancialFact] = []
    for concept in PEER_FINANCIAL_CONCEPTS:
        match = resolve_concept(concept, company_facts)
        if not match.matched or not match.facts or fy is None:
            results.append(
                CandidateFinancialFact(concept=concept, value=None, unit=None, xbrl_tag=match.tag, data_status="MISSING")
            )
            continue
        fact_entry, is_conflicting = _pick_fact_for_period(match.facts, fy, "FY")
        if fact_entry is None:
            results.append(
                CandidateFinancialFact(concept=concept, value=None, unit=match.unit, xbrl_tag=match.tag, data_status="MISSING")
            )
            continue
        results.append(
            CandidateFinancialFact(
                concept=concept,
                value=fact_entry.get("val"),
                unit=match.unit,
                xbrl_tag=fact_entry.get("_xbrl_tag", match.tag),
                data_status="CONFLICTING" if is_conflicting else "REPORTED",
            )
        )
    return results


def _operating_margin_from_financials(financials: list[CandidateFinancialFact]) -> Optional[float]:
    """Operating margin % = operating_income / revenue, REPORTED-only on both sides."""
    by_concept = {f.concept: f for f in financials}
    revenue = by_concept.get("revenue")
    op_income = by_concept.get("operating_income")
    if (
        revenue is None
        or op_income is None
        or revenue.data_status != "REPORTED"
        or op_income.data_status != "REPORTED"
        or not revenue.value
    ):
        return None
    return (op_income.value / revenue.value) * 100.0


def _revenue_cagr_pct(client: SECConnector, cik10: str) -> Optional[float]:
    """Revenue CAGR % over the CIK's available REPORTED history, reusing
    backend.data.historical_analysis (no duplicated CAGR math). Returns None
    (never fabricated) if fewer than 2 REPORTED revenue years are available."""
    try:
        company_facts = client.get_company_facts(cik10)
    except SECError:
        return None

    years = available_fiscal_years(company_facts, fiscal_period="FY", concepts=["revenue"])
    if len(years) < 2:
        return None

    facts = normalize_company_facts_multi_year(
        company_id=0,
        company_facts=company_facts,
        fiscal_years=years,
        fiscal_period="FY",
        cik10=cik10,
        concepts=["revenue"],
    )
    year_values = [
        _YearValueShim(fiscal_year=f.fiscal_year, value=f.value, data_status=f.data_status.value)
        for f in facts
        if f.concept == "revenue"
    ]
    year_values.sort(key=lambda y: y.fiscal_year)
    cagr = compute_cagr(year_values, concept="revenue")
    return cagr.cagr_pct if not cagr.insufficient_history else None


@dataclass
class _YearValueShim:
    """Local stand-in matching historical_analysis.YearValue's shape (avoids
    importing a private/internal name; compute_cagr only reads these three
    attributes)."""

    fiscal_year: int
    value: Optional[float]
    data_status: str


def _similarity_from_diff(candidate: Optional[float], target: Optional[float], scale: float) -> Optional[float]:
    """0..1 similarity score from an absolute difference, using a soft decay
    (1 / (1 + |diff| / scale)). `scale` sets how quickly similarity decays
    (e.g. scale=10 means a 10-point gap in margin% roughly halves the score).
    None in, None out -- no fabrication when either side is missing."""
    if candidate is None or target is None:
        return None
    diff = abs(candidate - target)
    return 1.0 / (1.0 + diff / scale)


def _composite_similarity(
    *,
    revenue_proximity_score: Optional[float],
    margin_similarity_score: Optional[float],
    growth_similarity_score: Optional[float],
    weights: tuple[float, float, float] = (0.3, 0.35, 0.35),
) -> tuple[float, float]:
    """Weighted average of the 3 dimensions that are actually available,
    then multiplied by data_completeness (fraction of dims available) so a
    candidate missing data ranks below an equally-similar candidate with
    full data, rather than being scored as if it were equally trustworthy.

    Returns (composite_score, data_completeness).
    """
    dims = [revenue_proximity_score, margin_similarity_score, growth_similarity_score]
    available = [(d, w) for d, w in zip(dims, weights) if d is not None]
    data_completeness = len(available) / len(dims)
    if not available:
        return 0.0, 0.0
    weight_sum = sum(w for _, w in available)
    weighted_avg = sum(d * w for d, w in available) / weight_sum
    return weighted_avg * data_completeness, data_completeness


def discover_peer_candidates(
    client: SECConnector,
    target_cik10: str,
    fiscal_year: int,
    quarter: Optional[int] = None,
    *,
    frame_concept_tag: str = DEFAULT_FRAME_CONCEPT_TAG,
    max_shortlist: int = 15,
    fetch_financials: bool = True,
) -> PeerDiscoveryResult:
    """Discover candidate comps peers for ``target_cik10`` via the SEC frames API.

    Steps:
    1. Classify the target's business type from its own SIC code (submissions).
    2. Pull the frame for ``frame_concept_tag`` (duration concept, e.g. Revenues)
       for the requested period -- this returns every company that reported
       that concept for the period (can be thousands).
    3. Rank the frame by proximity to the target's own reported value for that
       concept (closest-revenue-size peers are more useful comps peers), take
       the top ``max_shortlist`` candidates (excluding the target itself)
       BEFORE fetching any per-candidate submissions -- this bounds SEC
       traffic to a small, cached shortlist instead of the whole frame.
    4. For each shortlisted candidate, fetch (cached) submissions to read its
       SIC code and filter to the same business classification category as
       the target.
    5. Optionally (``fetch_financials``) pull each surviving candidate's real
       financial facts for a CompPeer-shaped preview, honestly marking
       missing concepts.

    Returns a PeerDiscoveryResult -- a shortlist for a human/caller to review,
    never auto-applied to a comps run.
    """
    target_submissions = client.get_submissions(target_cik10)
    target_classification = classify_business(target_submissions.get("sic"))

    frame = client.get_frame(frame_concept_tag, fiscal_year, quarter, instant=False)
    frame_data: list[dict[str, Any]] = frame.get("data", []) or []

    target_cik_int = int(target_cik10)
    target_entry = next((d for d in frame_data if d.get("cik") == target_cik_int), None)
    target_value = target_entry.get("val") if target_entry else None

    others = [d for d in frame_data if d.get("cik") != target_cik_int]

    if target_value is not None:
        others.sort(key=lambda d: abs((d.get("val") or 0) - target_value))
    # else: no size-based signal available: keep frame order (still bounded below).

    shortlist_raw = others[:max_shortlist]

    ticker_map = client._load_ticker_map()  # noqa: SLF001 -- reuse existing cached ticker map

    # Target's own margin/growth profile, computed once, used as the similarity
    # baseline below. Real REPORTED-derived only -- None (not fabricated) if
    # the target's own financials don't support the computation.
    target_margin_pct: Optional[float] = None
    target_growth_pct: Optional[float] = None
    if fetch_financials:
        target_financials = _fetch_candidate_financials(client, target_cik10)
        target_margin_pct = _operating_margin_from_financials(target_financials)
        target_growth_pct = _revenue_cagr_pct(client, target_cik10)

    # Widest revenue gap in the raw shortlist, used to normalize the
    # proximity signal already computed above (sort by abs diff) into a 0..1
    # score comparable to the margin/growth similarity scores.
    max_revenue_gap = 0.0
    if target_value is not None and shortlist_raw:
        max_revenue_gap = max(abs((d.get("val") or 0) - target_value) for d in shortlist_raw) or 1.0

    candidates: list[PeerCandidate] = []
    for entry in shortlist_raw:
        cik10 = str(entry.get("cik")).zfill(10)
        try:
            submissions = client.get_submissions(cik10)
        except SECError:
            continue  # can't confirm classification without submissions -- skip, don't guess

        classification = classify_business(submissions.get("sic"))
        if classification.category != target_classification.category:
            continue

        financials = _fetch_candidate_financials(client, cik10) if fetch_financials else []

        similarity: Optional[SimilarityScore] = None
        if fetch_financials:
            caveats: list[str] = []

            revenue_proximity_score: Optional[float] = None
            if target_value is not None and max_revenue_gap:
                gap = abs((entry.get("val") or 0) - target_value)
                revenue_proximity_score = max(0.0, 1.0 - gap / max_revenue_gap)
            else:
                caveats.append("Revenue proximity unavailable: target did not report the frame concept.")

            candidate_margin_pct = _operating_margin_from_financials(financials)
            margin_similarity_score = _similarity_from_diff(candidate_margin_pct, target_margin_pct, scale=10.0)
            if margin_similarity_score is None:
                caveats.append(
                    "Margin similarity unavailable: missing REPORTED revenue/operating_income for the "
                    "candidate and/or the target -- not scored as similar."
                )

            candidate_growth_pct = _revenue_cagr_pct(client, cik10)
            growth_similarity_score = _similarity_from_diff(candidate_growth_pct, target_growth_pct, scale=15.0)
            if growth_similarity_score is None:
                caveats.append(
                    "Growth similarity unavailable: fewer than 2 REPORTED revenue years for the candidate "
                    "and/or the target -- not scored as similar."
                )

            composite_score, data_completeness = _composite_similarity(
                revenue_proximity_score=revenue_proximity_score,
                margin_similarity_score=margin_similarity_score,
                growth_similarity_score=growth_similarity_score,
            )

            similarity = SimilarityScore(
                revenue_proximity_score=revenue_proximity_score,
                operating_margin_pct=candidate_margin_pct,
                revenue_cagr_pct=candidate_growth_pct,
                margin_similarity_score=margin_similarity_score,
                growth_similarity_score=growth_similarity_score,
                data_completeness=data_completeness,
                composite_score=composite_score,
                caveats=caveats,
            )

        candidates.append(
            PeerCandidate(
                cik10=cik10,
                ticker=_ticker_for_cik(cik10, ticker_map),
                entity_name=entry.get("entityName", submissions.get("name", "")),
                sic=submissions.get("sic"),
                sic_description=submissions.get("sicDescription"),
                classification=classification,
                frame_concept=frame_concept_tag,
                frame_value=entry.get("val"),
                financials=financials,
                similarity=similarity,
            )
        )

    # Re-rank by composite similarity (margin+growth+revenue-proximity),
    # not just revenue-proximity, when we actually computed it. Candidates
    # missing similarity data (fetch_financials=False) keep frame order.
    if fetch_financials:
        candidates.sort(
            key=lambda c: c.similarity.composite_score if c.similarity else 0.0,
            reverse=True,
        )

    note = None
    if target_value is None:
        note = (
            f"Target CIK {target_cik10} did not report '{frame_concept_tag}' for this period in the frame; "
            "shortlist could not be size-ranked and uses frame order instead."
        )

    return PeerDiscoveryResult(
        target_cik10=target_cik10,
        target_classification=target_classification,
        frame_concept=frame_concept_tag,
        fiscal_year=fiscal_year,
        quarter=quarter,
        frame_company_count=len(frame_data),
        candidates=candidates,
        note=note,
    )
