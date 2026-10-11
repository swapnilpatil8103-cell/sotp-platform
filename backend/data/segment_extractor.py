"""Segment discovery and extraction from a company's inline-XBRL 10-K.

SEC's XBRL "companyfacts" JSON API (`backend.services.sec_client.get_company_facts`)
only exposes *consolidated* facts -- it strips XBRL dimensional qualifiers.
Segment-level disclosures (e.g. Alphabet's Google Services / Google Cloud /
Other Bets revenue and operating income) are tagged with an XBRL dimension,
most commonly ``us-gaap:StatementBusinessSegmentsAxis``, which only survives
in the filing's actual XBRL instance document. Since ~2019 that instance is
embedded as *inline XBRL* directly in the primary 10-K HTML document filers
submit to EDGAR.

This module fetches that primary document via `SECConnector.get_filing_xbrl`
(`backend.services.sec_client`), parses its contexts/facts, and reconstructs
per-segment financial facts with the same full-provenance discipline as
`backend.data.normalizer`: REPORTED/MISSING data_status, source, xbrl_tag,
accession_number, source_url. It never fabricates a segment metric that
isn't disclosed. If no segment-dimensional facts are found at all
(single-segment filer, or a filer whose segment note isn't XBRL-tagged), it
returns an empty segment list with an explanatory note.
"""

from __future__ import annotations

from datetime import date
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

from backend.data.concept_mapping import REVENUE_CONTRACT_TAGS
from backend.data.xbrl_instance import XbrlContext, XbrlFact
from backend.models.enums import DataStatus
from backend.models.segment_financial_fact import SegmentFinancialFact

if TYPE_CHECKING:
    from backend.services.sec_client import SECConnector

SEC_SOURCE = "SEC XBRL (inline, filing instance)"

# Canonical segment-level concepts we attempt to extract, mapped to candidate
# US-GAAP tag local names (same vocabulary as the company-level concept map,
# just dimensionally qualified in the instance document).
SEGMENT_CONCEPT_TAG_CANDIDATES: dict[str, list[str]] = {
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        # Banks tag segment (and consolidated) total net revenue this way (JPM's segments).
        "RevenuesNetOfInterestExpense",
    ],
    "operating_income": [
        "OperatingIncomeLoss",
    ],
    "da": [
        "DepreciationDepletionAndAmortization",
        "DepreciationAmortizationAndAccretionNet",
        "DepreciationAndAmortization",
    ],
    "capex": [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsForCapitalImprovements",
        "PaymentsToAcquireProductiveAssets",
    ],
    "assets": [
        "Assets",
    ],
}
SEGMENT_CONCEPTS: list[str] = list(SEGMENT_CONCEPT_TAG_CANDIDATES.keys())
INSTANT_CONCEPTS = {"assets"}

SEGMENT_AXIS_LOCAL_NAMES = {
    "StatementBusinessSegmentsAxis",
    "SegmentAxis",
    "BusinessSegmentAxis",
    "OperatingSegmentsAxis",
}

# `ConsolidationItemsAxis` (member `OperatingSegmentsMember`) is boilerplate
# that many filers (e.g. Alphabet's segment operating-income table, Apple's
# geographic revenue table) attach alongside the business-segment axis purely
# to say "this is the reportable-segment amount, not corporate/eliminations".
# It is not a further breakdown of the segment (unlike ProductOrServiceAxis,
# a geography axis used as an *additional* qualifier, etc.), so a context
# carrying only the segment axis plus this specific axis/member still counts
# as the segment's own total, not a sub-component.
NON_BREAKDOWN_AXIS_MEMBERS = {
    "ConsolidationItemsAxis": {"OperatingSegmentsMember"},
}


def _is_pure_segment_context(ctx: "XbrlContext") -> bool:
    """True if every dimension on this context is either the business-segment
    axis itself or a recognized non-breakdown qualifier (see
    ``NON_BREAKDOWN_AXIS_MEMBERS``) -- i.e. the fact represents the segment's
    own total, not a finer product/geography/etc. sub-line nested within it.
    """
    for axis, member in ctx.dimensions:
        axis_local = _local(axis)
        if axis_local in SEGMENT_AXIS_LOCAL_NAMES:
            continue
        allowed_members = NON_BREAKDOWN_AXIS_MEMBERS.get(axis_local)
        if allowed_members is not None and _local(member) in allowed_members:
            continue
        return False
    return True


def _local(qname: str) -> str:
    return qname.split(":")[-1]


def _humanize(local: str) -> str:
    if local.endswith("Member"):
        local = local[: -len("Member")]
    out = []
    for i, ch in enumerate(local):
        if ch.isupper() and i > 0 and (not local[i - 1].isupper() or (i + 1 < len(local) and local[i + 1].islower())):
            out.append(" ")
        out.append(ch)
    return "".join(out).strip()


# Total-revenue tags: when one of these is reported for a segment alongside a contract-revenue tag,
# the contract figure is a subset (Berkshire's BNSF: Revenues 23,533M vs contract revenue 23,256M).
REVENUE_TOTAL_TAGS = ("Revenues", "RevenuesNetOfInterestExpense")
CONSOLIDATION_AXIS = "ConsolidationItemsAxis"
OPERATING_SEGMENTS_MEMBER = "OperatingSegmentsMember"


@dataclass
class ExtractedSegment:
    name: str
    facts: list[SegmentFinancialFact] = field(default_factory=list)
    # Name of the listed segment this member is nested under (from the filing's definition
    # linkbase), whose amounts already include this member's. None for a top-level segment.
    parent_segment: Optional[str] = None


@dataclass
class SegmentExtractionResult:
    segments: list[ExtractedSegment]
    note: Optional[str] = None


def _segment_contexts(contexts: dict[str, XbrlContext]) -> dict[str, list[str]]:
    """Map segment display name -> list of context ids qualified by that segment."""
    result: dict[str, list[str]] = {}
    for ctx_id, ctx in contexts.items():
        for axis, member in ctx.dimensions:
            if _local(axis) in SEGMENT_AXIS_LOCAL_NAMES:
                name = _humanize(_local(member))
                result.setdefault(name, []).append(ctx_id)
    return result


def _segment_member_locals(contexts: dict[str, XbrlContext]) -> dict[str, str]:
    """Display name -> the member's local XBRL name (the key the definition linkbase uses)."""
    result: dict[str, str] = {}
    for ctx in contexts.values():
        for axis, member in ctx.dimensions:
            if _local(axis) in SEGMENT_AXIS_LOCAL_NAMES:
                result[_humanize(_local(member))] = _local(member)
    return result


def _is_annual_duration(start: Optional[date], end: Optional[date]) -> bool:
    if not start or not end:
        return False
    return 350 <= (end - start).days <= 380


def _is_quarter_duration(start: Optional[date], end: Optional[date]) -> bool:
    if not start or not end:
        return False
    return 80 <= (end - start).days <= 100


def _context_matches_period(ctx: XbrlContext, fiscal_year: int, fiscal_period: str) -> bool:
    if fiscal_period == "FY":
        return bool(ctx.end) and ctx.end.year == fiscal_year and (_is_annual_duration(ctx.start, ctx.end) or ctx.instant is not None)
    return bool(ctx.end) and ctx.end.year == fiscal_year and (_is_quarter_duration(ctx.start, ctx.end) or ctx.instant is not None)


@dataclass
class _Hit:
    priority: int  # position of the tag in the candidate list (lower = preferred)
    rank: int  # 0 = from the reportable-segment table (ConsolidationItemsAxis=OperatingSegments), 1 = other
    order: int  # document order
    tag: str
    value: float


def _is_reportable_table_context(ctx: XbrlContext) -> bool:
    return any(_local(a) == CONSOLIDATION_AXIS and _local(m) == OPERATING_SEGMENTS_MEMBER for a, m in ctx.dimensions)


def _fmt_amount(value: float) -> str:
    return f"{value:,.0f}"


def _pick_segment_value(
    concept: str,
    candidates: list[str],
    pure_ctx_ids: list[str],
    contexts: dict[str, XbrlContext],
    facts_index: dict[tuple[str, str], list[XbrlFact]],
) -> Optional[tuple[_Hit, DataStatus, Optional[str]]]:
    """Choose a segment's value for one concept among every matching tag and context.

    Preference: the highest-priority tag, then a value from the reportable-segment table, then
    document order. For revenue a larger total-revenue tag supersedes a contract-revenue
    subset. If the chosen tag has different values in different disclosures for the same
    segment and period, the result is CONFLICTING and the note says which disagree.
    """
    hits: list[_Hit] = []
    order = 0
    for priority, tag in enumerate(candidates):
        for cid in pure_ctx_ids:
            rank = 0 if _is_reportable_table_context(contexts[cid]) else 1
            for f in facts_index.get((tag, cid), []):
                hits.append(_Hit(priority, rank, order, tag, f.value))
                order += 1
    if not hits:
        return None

    def key(h: _Hit) -> tuple[int, int, int]:
        return (h.priority, h.rank, h.order)

    hits.sort(key=key)
    chosen = hits[0]
    note: Optional[str] = None
    if concept == "revenue" and chosen.tag in REVENUE_CONTRACT_TAGS:
        totals = [h for h in hits if h.tag in REVENUE_TOTAL_TAGS and h.value > chosen.value]
        if totals:
            total = min(totals, key=key)
            note = (
                f"Total revenue ({total.tag} {_fmt_amount(total.value)}) used: the contract-revenue tag "
                f"({chosen.tag} {_fmt_amount(chosen.value)}) for this segment is a subset of it."
            )
            chosen = total

    same_tag = [h for h in hits if h.tag == chosen.tag]
    if len({h.value for h in same_tag}) > 1:
        seen: dict[tuple[float, int], None] = {}
        for h in same_tag:
            seen[(h.value, h.rank)] = None
        listing = "; ".join(
            f"{_fmt_amount(v)} ({'reportable-segment table' if r == 0 else 'other segment disclosure'})" for v, r in seen
        )
        conflict = f"{chosen.tag} differs between disclosures for this segment: {listing}. The reportable-segment table value is used where present."
        return chosen, DataStatus.CONFLICTING, (f"{note} {conflict}" if note else conflict)
    return chosen, DataStatus.REPORTED, note


def _reconciling_items(
    contexts: dict[str, XbrlContext],
    facts_index: dict[tuple[str, str], list[XbrlFact]],
    fiscal_year: int,
    fiscal_period: str,
) -> list[str]:
    """Amounts filed against ConsolidationItemsAxis members that are not operating segments
    (intersegment eliminations, corporate/non-segment items). They are not segments, but without
    them the segments' amounts do not add up to the consolidated figures."""
    out: dict[str, dict[str, float]] = {}
    for cid, ctx in contexts.items():
        if len(ctx.dimensions) != 1 or _local(ctx.dimensions[0][0]) != CONSOLIDATION_AXIS:
            continue
        member = _local(ctx.dimensions[0][1])
        if member == OPERATING_SEGMENTS_MEMBER or not _context_matches_period(ctx, fiscal_year, fiscal_period):
            continue
        for concept in ("revenue", "operating_income"):
            for tag in SEGMENT_CONCEPT_TAG_CANDIDATES[concept]:
                hit = facts_index.get((tag, cid))
                if hit:
                    out.setdefault(_humanize(member), {})[concept] = hit[0].value
                    break
    return [
        f"{name}: " + ", ".join(f"{concept} {_fmt_amount(v)}" for concept, v in sorted(vals.items()))
        for name, vals in sorted(out.items())
    ]


def extract_segments_from_instance(
    contexts: dict[str, XbrlContext],
    facts: list[XbrlFact],
    fiscal_year: int,
    fiscal_period: str = "FY",
    cik10: Optional[str] = None,
    accession_number: Optional[str] = None,
    source_url: Optional[str] = None,
    member_parents: Optional[dict[str, set[str]]] = None,
) -> SegmentExtractionResult:
    """Reconstruct per-segment facts from a filing's inline-XBRL contexts and facts.

    ``member_parents`` (child member local name -> parent member local names, from the filing's
    definition linkbase) lets the result flag a sub-segment with ``parent_segment`` so callers do
    not add it to its parent. Members with no value for the period (recast or discontinued
    segments still present for prior periods) are left out of ``segments`` and named in
    ``note``; reconciling items (eliminations, corporate) are summarised there too.
    """
    seg_ctx_map = _segment_contexts(contexts)

    if not seg_ctx_map:
        return SegmentExtractionResult(
            segments=[],
            note=(
                "No segment-dimensional XBRL facts found in this filing's instance "
                "document. Likely a single-segment reporter, or segment data is "
                "disclosed only in prose/tables not tagged with a business-segment axis."
            ),
        )

    # Index facts by (tag_local_name, context_id) for fast lookup.
    facts_index: dict[tuple[str, str], list[XbrlFact]] = {}
    for f in facts:
        if f.value is None:
            continue
        facts_index.setdefault((_local(f.name), f.context_id), []).append(f)

    segments: list[ExtractedSegment] = []
    for segment_name, ctx_ids in sorted(seg_ctx_map.items()):
        matching_ctx_ids = [
            cid for cid in ctx_ids if cid in contexts and _context_matches_period(contexts[cid], fiscal_year, fiscal_period)
        ]

        # Only contexts dimensioned *purely* by the business-segment axis (no
        # additional axes layered on top, e.g. ProductOrServiceAxis or
        # geography) represent the segment's own total. A context with extra
        # dimensions is a finer sub-component nested within the segment (a
        # product line, a region, etc.) and must never be mistaken for the
        # segment-level total -- see module docstring / bug history.
        pure_ctx_ids = [cid for cid in matching_ctx_ids if _is_pure_segment_context(contexts[cid])]

        ext = ExtractedSegment(name=segment_name)
        for concept in SEGMENT_CONCEPTS:
            candidates = SEGMENT_CONCEPT_TAG_CANDIDATES[concept]
            picked = _pick_segment_value(concept, candidates, pure_ctx_ids, contexts, facts_index)
            # Deliberately no fallback to multi-dimensional (e.g.
            # product-level) facts here: if a filer never discloses a pure
            # segment-level total for this concept, summing or guessing from
            # sub-line facts would risk double-counting or omitting lines we
            # can't verify are exhaustive. Report MISSING instead.

            if picked is None:
                ext.facts.append(
                    SegmentFinancialFact(
                        segment_id=0,
                        concept=concept,
                        value=None,
                        unit="USD",
                        currency="USD",
                        period=f"{fiscal_year}-{fiscal_period}",
                        fiscal_year=fiscal_year,
                        fiscal_period=fiscal_period,
                        filing_date=None,
                        source=SEC_SOURCE,
                        source_url=None,
                        accession_number=accession_number,
                        xbrl_tag=candidates[0] if candidates else None,
                        data_status=DataStatus.MISSING,
                    )
                )
                continue

            hit, status, note = picked
            ext.facts.append(
                SegmentFinancialFact(
                    segment_id=0,
                    concept=concept,
                    value=hit.value,
                    unit="USD",
                    currency="USD",
                    period=f"{fiscal_year}-{fiscal_period}",
                    fiscal_year=fiscal_year,
                    fiscal_period=fiscal_period,
                    filing_date=None,
                    source=SEC_SOURCE,
                    source_url=source_url,
                    accession_number=accession_number,
                    xbrl_tag=f"us-gaap:{hit.tag}",
                    data_status=status,
                    status_note=note,
                )
            )
        segments.append(ext)

    reported = [s for s in segments if any(f.data_status != DataStatus.MISSING for f in s.facts)]
    omitted = sorted(s.name for s in segments if s not in reported)

    # Nesting, from the filing's own definition linkbase.
    parents = member_parents or {}
    local_by_name = _segment_member_locals(contexts)
    name_by_local = {local_by_name[s.name]: s.name for s in reported if s.name in local_by_name}
    for s in reported:
        own = local_by_name.get(s.name)
        for parent_local in sorted(parents.get(own, set())):
            parent_name = name_by_local.get(parent_local)
            if parent_name is None or parent_name == s.name:
                continue
            if own in parents.get(parent_local, set()):  # mutual: ambiguous, flag neither
                continue
            s.parent_segment = parent_name
            break

    notes: list[str] = []
    if omitted:
        notes.append(
            f"Segment members with no reported value for {fiscal_period} {fiscal_year} are not listed "
            f"(typically segments that were recast or discontinued and appear only in other periods): {', '.join(omitted)}."
        )
    nested = sorted(f"{s.name} (in {s.parent_segment})" for s in reported if s.parent_segment)
    if nested:
        notes.append(
            "These members are nested under another listed segment, whose amounts already include theirs; "
            "do not add them to the parent: " + "; ".join(nested) + "."
        )
    recon = _reconciling_items(contexts, facts_index, fiscal_year, fiscal_period)
    if recon:
        notes.append(
            "Reconciling items that are not segments (segment amounts need them to add up to the consolidated figures): "
            + "; ".join(recon) + "."
        )
    return SegmentExtractionResult(segments=reported, note=" ".join(notes) or None)


def extract_segments_for_filing(
    connector: "SECConnector",
    cik10: str,
    fiscal_year: int,
    fiscal_period: str,
    filing: dict[str, Any],
) -> SegmentExtractionResult:
    """Fetch + parse a specific filing's inline-XBRL document and extract segments.

    ``filing`` is a dict as returned by ``SECConnector.get_latest_filings`` /
    ``get_filing_metadata`` (has ``source_url`` pointing at the primary
    document and ``accession_number``). ``connector`` is used (rather than a
    bare user-agent string) so the fetch goes through the connector's shared
    caching/rate-limiting/typed-error handling via ``get_filing_xbrl``.
    """
    contexts, facts = connector.get_filing_xbrl(filing["source_url"])
    hierarchy_fetch = getattr(connector, "get_filing_member_hierarchy", None)
    member_parents = hierarchy_fetch(filing["source_url"]) if hierarchy_fetch is not None else None
    return extract_segments_from_instance(
        contexts,
        facts,
        fiscal_year=fiscal_year,
        fiscal_period=fiscal_period,
        cik10=cik10,
        accession_number=filing.get("accession_number"),
        source_url=filing["source_url"],
        member_parents=member_parents,
    )
