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
from backend.data.normalizer import _pick_fact_for_period
from backend.services.sec_client import SECClient, SECError

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


def _fetch_candidate_financials(client: SECClient, cik10: str) -> list[CandidateFinancialFact]:
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
        fact_entry = _pick_fact_for_period(match.facts, fy, "FY")
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
                xbrl_tag=match.tag,
                data_status="REPORTED",
            )
        )
    return results


def discover_peer_candidates(
    client: SECClient,
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
            )
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
