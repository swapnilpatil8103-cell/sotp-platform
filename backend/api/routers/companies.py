"""Companies router — resolves tickers via SEC EDGAR and returns basic company info."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from backend.api.deps import get_sec_client
from backend.data.normalizer import latest_available_fiscal_year, normalize_company_facts
from backend.data.peer_discovery import discover_peer_candidates
from backend.data.persistence import (
    facts_are_fresh,
    get_or_create_company,
    get_persisted_facts,
    upsert_financial_facts,
)
from backend.db import get_session
from backend.schemas.company import CompanyFactsRead, CompanyRead, FinancialFactRead
from backend.services.sec_client import SECError, SECNotFoundError, SECRateLimitError, SECUnavailableError

router = APIRouter(prefix="/companies", tags=["companies"])


@router.get("")
def list_companies():
    # Company listing requires a persisted catalog (DB-backed); the platform only
    # resolves companies on demand from SEC EDGAR for now.
    raise HTTPException(
        status_code=501,
        detail="Not implemented: companies listing requires a persisted company catalog (not yet built)",
    )


@router.get("/{ticker}", response_model=CompanyRead)
def get_company(
    ticker: str,
    session: Session = Depends(get_session),
    client=Depends(get_sec_client),
) -> CompanyRead:
    try:
        cik10 = client.get_cik(ticker)
        submissions = client.get_submissions(cik10)
    except SECNotFoundError:
        raise HTTPException(status_code=404, detail=f"Unknown ticker: {ticker}")
    except SECRateLimitError:
        raise HTTPException(status_code=503, detail="SEC EDGAR rate limit exceeded, please retry shortly")
    except SECUnavailableError:
        raise HTTPException(status_code=503, detail="SEC EDGAR is currently unavailable, please retry shortly")
    except SECError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    name = submissions.get("name", "")
    # Persist (or look up) the internal Company row so the response can
    # return company_id -- this is the ticker->company_id resolution the
    # frontend needs (Research History, Value Unlock, AI Analyst pages),
    # reusing the same get_or_create_company path /facts and /segments use.
    company = get_or_create_company(session, ticker=ticker, cik10=cik10, name=name)

    return CompanyRead(
        ticker=ticker.upper(),
        cik=cik10,
        name=name,
        sic=submissions.get("sic"),
        sic_description=submissions.get("sicDescription"),
        exchange=(submissions.get("exchanges") or [None])[0],
        fiscal_year_end=submissions.get("fiscalYearEnd"),
        company_id=company.id,
    )


@router.get("/{ticker}/id")
def get_company_id(
    ticker: str,
    session: Session = Depends(get_session),
    client=Depends(get_sec_client),
) -> dict:
    """Dedicated ticker->company_id resolution endpoint.

    Thin wrapper around the same lookup GET /companies/{ticker} now does
    (and persists a Company row via get_or_create_company if one doesn't
    exist yet), for callers that only need the id without the rest of the
    SEC submissions payload.
    """
    company = get_company(ticker, session=session, client=client)
    return {"ticker": ticker.upper(), "company_id": company.company_id}


@router.get("/{ticker}/filings")
def get_company_filings(ticker: str):
    client = get_sec_client()
    try:
        cik10 = client.get_cik(ticker)
        filings = client.get_latest_filings(cik10, form_types=("10-K", "10-Q"))
    except SECNotFoundError:
        raise HTTPException(status_code=404, detail=f"Unknown ticker: {ticker}")
    except SECRateLimitError:
        raise HTTPException(status_code=503, detail="SEC EDGAR rate limit exceeded, please retry shortly")
    except SECUnavailableError:
        raise HTTPException(status_code=503, detail="SEC EDGAR is currently unavailable, please retry shortly")
    except SECError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    if not filings:
        raise HTTPException(status_code=404, detail=f"No 10-K/10-Q filings found for {ticker}")
    return {"ticker": ticker.upper(), "cik": cik10, "filings": filings}


@router.get("/{ticker}/facts", response_model=CompanyFactsRead)
def get_company_facts(
    ticker: str,
    fiscal_year: int | None = None,
    fiscal_period: str = "FY",
    session: Session = Depends(get_session),
) -> CompanyFactsRead:
    client = get_sec_client()
    try:
        cik10 = client.get_cik(ticker)
    except SECNotFoundError:
        raise HTTPException(status_code=404, detail=f"Unknown ticker or no XBRL facts available: {ticker}")
    except SECRateLimitError:
        raise HTTPException(status_code=503, detail="SEC EDGAR rate limit exceeded, please retry shortly")
    except SECUnavailableError:
        raise HTTPException(status_code=503, detail="SEC EDGAR is currently unavailable, please retry shortly")
    except SECError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    company = get_or_create_company(session, ticker=ticker, cik10=cik10)

    # If we already have persisted facts for a specific requested fiscal_year,
    # serve them from the DB without re-hitting SEC EDGAR.
    if fiscal_year is not None and facts_are_fresh(session, company.id, fiscal_year, fiscal_period):
        persisted = get_persisted_facts(session, company.id, fiscal_year, fiscal_period)
        return CompanyFactsRead(
            ticker=ticker.upper(),
            cik=cik10,
            fiscal_year=fiscal_year,
            fiscal_period=fiscal_period,
            facts=[_to_read(f) for f in persisted],
        )

    try:
        company_facts = client.get_company_facts(cik10)
    except SECNotFoundError:
        raise HTTPException(status_code=404, detail=f"Unknown ticker or no XBRL facts available: {ticker}")
    except SECRateLimitError:
        raise HTTPException(status_code=503, detail="SEC EDGAR rate limit exceeded, please retry shortly")
    except SECUnavailableError:
        raise HTTPException(status_code=503, detail="SEC EDGAR is currently unavailable, please retry shortly")
    except SECError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    target_year = fiscal_year or latest_available_fiscal_year(company_facts, fiscal_period)
    if target_year is None:
        raise HTTPException(status_code=404, detail=f"No {fiscal_period} XBRL facts available for {ticker}")

    facts = normalize_company_facts(
        company_id=company.id,
        company_facts=company_facts,
        fiscal_year=target_year,
        fiscal_period=fiscal_period,
        cik10=cik10,
    )
    persisted = upsert_financial_facts(session, company.id, facts)

    return CompanyFactsRead(
        ticker=ticker.upper(),
        cik=cik10,
        fiscal_year=target_year,
        fiscal_period=fiscal_period,
        facts=[_to_read(f) for f in persisted],
    )


@router.get("/{ticker}/peer-candidates")
def get_peer_candidates(
    ticker: str,
    fiscal_year: int,
    quarter: int | None = None,
    frame_concept: str = "Revenues",
    max_shortlist: int = 15,
    client=Depends(get_sec_client),
) -> dict:
    """Discover candidate comps peers for `ticker` via the real SEC XBRL
    "frames" API (Phase 10 addition).

    Read-only research/discovery endpoint: it sources real candidate peers
    (same SIC-code business classification, real reported financials with
    honest REPORTED/MISSING provenance) but does NOT create or mutate any
    AssumptionDecision, ValuationRun, or other governance-tracked state, and
    it does NOT run backend.valuation.comps itself. Exactly like AI peer
    recommendation (Phase 6) and manual peer entry, a discovered candidate
    must still be explicitly reviewed/selected by a human (or an explicit
    caller choice) before it's used in an actual comps run -- see
    docs/valuation-methodology.md.

    `fiscal_year`/`quarter` select the frame period (quarter omitted = full
    fiscal year duration frame); `frame_concept` is the US-GAAP duration tag
    used to define "who reported this period" (default Revenues).
    """
    try:
        cik10 = client.get_cik(ticker)
        result = discover_peer_candidates(
            client,
            target_cik10=cik10,
            fiscal_year=fiscal_year,
            quarter=quarter,
            frame_concept_tag=frame_concept,
            max_shortlist=max_shortlist,
        )
    except SECNotFoundError:
        raise HTTPException(status_code=404, detail=f"Unknown ticker or no frame data available: {ticker}")
    except SECRateLimitError:
        raise HTTPException(status_code=503, detail="SEC EDGAR rate limit exceeded, please retry shortly")
    except SECUnavailableError:
        raise HTTPException(status_code=503, detail="SEC EDGAR is currently unavailable, please retry shortly")
    except SECError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    return {
        "ticker": ticker.upper(),
        "cik": result.target_cik10,
        "target_classification": {
            "category": result.target_classification.category,
            "sic_code": result.target_classification.sic_code,
            "rationale": result.target_classification.rationale,
        },
        "frame_concept": result.frame_concept,
        "fiscal_year": result.fiscal_year,
        "quarter": result.quarter,
        "frame_company_count": result.frame_company_count,
        "note": result.note,
        "candidates": [
            {
                "cik": c.cik10,
                "ticker": c.ticker,
                "entity_name": c.entity_name,
                "sic": c.sic,
                "sic_description": c.sic_description,
                "classification_category": c.classification.category,
                "frame_concept": c.frame_concept,
                "frame_value": c.frame_value,
                "financials": [
                    {
                        "concept": f.concept,
                        "value": f.value,
                        "unit": f.unit,
                        "xbrl_tag": f.xbrl_tag,
                        "data_status": f.data_status,
                    }
                    for f in c.financials
                ],
            }
            for c in result.candidates
        ],
        "governance_note": (
            "Discovery/research only -- no AssumptionDecision or ValuationRun was created or modified. "
            "A human (or explicit caller action) must still select and confirm peers before they are used "
            "in an actual comps run."
        ),
    }


def _to_read(f) -> FinancialFactRead:
    return FinancialFactRead(
        concept=f.concept,
        value=f.value,
        unit=f.unit,
        currency=f.currency,
        period=f.period,
        fiscal_year=f.fiscal_year,
        fiscal_period=f.fiscal_period,
        filing_date=f.filing_date,
        source=f.source,
        source_url=f.source_url,
        accession_number=f.accession_number,
        xbrl_tag=f.xbrl_tag,
        data_status=f.data_status.value,
    )
