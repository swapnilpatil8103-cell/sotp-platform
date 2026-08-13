"""Segment discovery and extraction from a company's inline-XBRL 10-K.

SEC's XBRL "companyfacts" JSON API (`backend.services.sec_client.get_company_facts`)
only exposes *consolidated* facts -- it strips XBRL dimensional qualifiers.
Segment-level disclosures (e.g. Alphabet's Google Services / Google Cloud /
Other Bets revenue and operating income) are tagged with an XBRL dimension,
most commonly ``us-gaap:StatementBusinessSegmentsAxis``, which only survives
in the filing's actual XBRL instance document. Since ~2019 that instance is
embedded as *inline XBRL* directly in the primary 10-K HTML document filers
submit to EDGAR.

This module fetches that primary document (via `backend.data.xbrl_instance`),
parses its contexts/facts, and reconstructs per-segment financial facts with
the same full-provenance discipline as `backend.data.normalizer`:
REPORTED/MISSING data_status, source, xbrl_tag, accession_number, source_url.
It never fabricates a segment metric that isn't disclosed. If no
segment-dimensional facts are found at all (single-segment filer, or a filer
whose segment note isn't XBRL-tagged), it returns an empty segment list with
an explanatory note.
"""

from __future__ import annotations

from datetime import date
from dataclasses import dataclass, field
from typing import Any, Optional

from backend.data.xbrl_instance import (
    XbrlContext,
    XbrlFact,
    fetch_inline_xbrl_document,
    parse_inline_xbrl,
)
from backend.models.enums import DataStatus
from backend.models.segment_financial_fact import SegmentFinancialFact

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


@dataclass
class ExtractedSegment:
    name: str
    facts: list[SegmentFinancialFact] = field(default_factory=list)


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


def extract_segments_from_instance(
    contexts: dict[str, XbrlContext],
    facts: list[XbrlFact],
    fiscal_year: int,
    fiscal_period: str = "FY",
    cik10: Optional[str] = None,
    accession_number: Optional[str] = None,
    source_url: Optional[str] = None,
) -> SegmentExtractionResult:
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

        ext = ExtractedSegment(name=segment_name)
        for concept in SEGMENT_CONCEPTS:
            candidates = SEGMENT_CONCEPT_TAG_CANDIDATES[concept]
            picked_fact: Optional[XbrlFact] = None
            picked_tag: Optional[str] = None
            for tag in candidates:
                for cid in matching_ctx_ids:
                    hits = facts_index.get((tag, cid))
                    if hits:
                        picked_fact = hits[0]
                        picked_tag = tag
                        break
                if picked_fact is not None:
                    break

            if picked_fact is None:
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

            ext.facts.append(
                SegmentFinancialFact(
                    segment_id=0,
                    concept=concept,
                    value=picked_fact.value,
                    unit="USD",
                    currency="USD",
                    period=f"{fiscal_year}-{fiscal_period}",
                    fiscal_year=fiscal_year,
                    fiscal_period=fiscal_period,
                    filing_date=None,
                    source=SEC_SOURCE,
                    source_url=source_url,
                    accession_number=accession_number,
                    xbrl_tag=f"us-gaap:{picked_tag}",
                    data_status=DataStatus.REPORTED,
                )
            )
        segments.append(ext)

    return SegmentExtractionResult(segments=segments, note=None)


def extract_segments_for_filing(
    user_agent: str,
    cik10: str,
    fiscal_year: int,
    fiscal_period: str,
    filing: dict[str, Any],
) -> SegmentExtractionResult:
    """Fetch + parse a specific filing's inline-XBRL document and extract segments.

    ``filing`` is a dict as returned by ``SECClient.get_latest_filings`` (has
    ``source_url`` pointing at the primary document and ``accession_number``).
    """
    document = fetch_inline_xbrl_document(filing["source_url"], user_agent=user_agent)
    contexts, facts = parse_inline_xbrl(document)
    return extract_segments_from_instance(
        contexts,
        facts,
        fiscal_year=fiscal_year,
        fiscal_period=fiscal_period,
        cik10=cik10,
        accession_number=filing.get("accession_number"),
        source_url=filing["source_url"],
    )
