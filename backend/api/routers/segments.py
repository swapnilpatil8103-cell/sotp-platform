"""Segments router — real implementation (Phase 3: segment data extraction).

Extracts per-segment financial facts (revenue, operating income, D&A, capex,
assets where disclosed) from a company's most recent 10-K's inline-XBRL
instance document, scores data coverage per segment, persists everything via
the DB, and returns the result with full provenance.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from backend.api.deps import get_sec_client
from backend.data.persistence import (
    get_or_create_company,
    get_or_create_segment,
    upsert_segment_financial_facts,
)
from backend.data.segment_coverage import compute_company_segment_coverage
from backend.data.segment_extractor import extract_segments_for_filing
from backend.data.normalizer import latest_available_fiscal_year
from backend.db import get_session
from backend.schemas.segment import (
    CompanySegmentsRead,
    MetricCoverageRead,
    SegmentFactRead,
    SegmentRead,
)
from backend.services.sec_client import SECError, SECNotFoundError, SECRateLimitError, SECUnavailableError

router = APIRouter(tags=["segments"])


@router.get("/segments")
def list_segments(ticker: str | None = None):
    if not ticker:
        raise HTTPException(status_code=400, detail="ticker query parameter is required")
    raise HTTPException(
        status_code=400,
        detail=f"Use GET /companies/{ticker}/segments instead",
    )


@router.get("/companies/{ticker}/segments", response_model=CompanySegmentsRead)
def get_company_segments(
    ticker: str,
    fiscal_year: int | None = None,
    session: Session = Depends(get_session),
) -> CompanySegmentsRead:
    client = get_sec_client()
    try:
        cik10 = client.get_cik(ticker)
        filings = client.get_latest_filings(cik10, form_types=("10-K",))
    except SECNotFoundError:
        raise HTTPException(status_code=404, detail=f"Unknown ticker: {ticker}")
    except SECRateLimitError:
        raise HTTPException(status_code=503, detail="SEC EDGAR rate limit exceeded, please retry shortly")
    except SECUnavailableError:
        raise HTTPException(status_code=503, detail="SEC EDGAR is currently unavailable, please retry shortly")
    except SECError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    if not filings:
        raise HTTPException(status_code=404, detail=f"No 10-K filing found for {ticker}")
    filing = filings[0]

    target_year = fiscal_year
    if target_year is None:
        try:
            company_facts = client.get_company_facts(cik10)
            target_year = latest_available_fiscal_year(company_facts, "FY")
        except SECError:
            target_year = None
        if target_year is None:
            period = filing.get("period_of_report") or ""
            target_year = int(period[:4]) if period[:4].isdigit() else None
    if target_year is None:
        raise HTTPException(status_code=404, detail=f"Could not determine a fiscal year for {ticker}")

    try:
        result = extract_segments_for_filing(
            user_agent=client.user_agent,
            cik10=cik10,
            fiscal_year=target_year,
            fiscal_period="FY",
            filing=filing,
        )
    except Exception as exc:  # network/parse failures against the filing document
        raise HTTPException(status_code=503, detail=f"Failed to extract segment data: {exc}")

    company = get_or_create_company(session, ticker=ticker, cik10=cik10)

    segments_facts_for_coverage = {}
    segment_reads: list[SegmentRead] = []
    for ext_segment in result.segments:
        db_segment = get_or_create_segment(session, company_id=company.id, name=ext_segment.name)
        for f in ext_segment.facts:
            f.segment_id = db_segment.id
        persisted = upsert_segment_financial_facts(session, db_segment.id, ext_segment.facts)
        segments_facts_for_coverage[ext_segment.name] = persisted

    coverage_by_segment, overall = compute_company_segment_coverage(segments_facts_for_coverage)

    for name, facts in segments_facts_for_coverage.items():
        cov = coverage_by_segment[name]
        segment_reads.append(
            SegmentRead(
                name=name,
                facts=[
                    SegmentFactRead(
                        concept=f.concept,
                        value=f.value,
                        unit=f.unit,
                        currency=f.currency,
                        period=f.period,
                        fiscal_year=f.fiscal_year,
                        fiscal_period=f.fiscal_period,
                        source=f.source,
                        source_url=f.source_url,
                        accession_number=f.accession_number,
                        xbrl_tag=f.xbrl_tag,
                        data_status=f.data_status.value,
                    )
                    for f in facts
                ],
                coverage=[
                    MetricCoverageRead(
                        metric=m.metric,
                        reported_periods=m.reported_periods,
                        total_periods=m.total_periods,
                        coverage_pct=m.coverage_pct,
                    )
                    for m in cov.metrics.values()
                ],
                overall_coverage_pct=cov.overall_coverage_pct,
            )
        )

    return CompanySegmentsRead(
        ticker=ticker.upper(),
        cik=cik10,
        fiscal_year=target_year,
        fiscal_period="FY",
        segments=segment_reads,
        overall_coverage_pct=overall,
        note=result.note,
    )
