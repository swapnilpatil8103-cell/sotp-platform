"""Companies router — resolves tickers via SEC EDGAR and returns basic company info."""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from backend.api.deps import get_sec_client
from backend.data.historical_analysis import build_historical_trends
from backend.data.normalizer import latest_available_fiscal_year, normalize_company_facts
from backend.data.peer_discovery import discover_peer_candidates
from backend.data.persistence import (
    facts_are_fresh,
    get_or_create_company,
    get_persisted_facts,
    upsert_financial_facts,
    upsert_insider_transactions,
    upsert_institutional_holdings,
)
from backend.db import get_session
from backend.models.enums import DataStatus
from backend.models.insider_transaction import InsiderTransaction
from backend.models.institutional_holding import InstitutionalHolding
from backend.schemas.company import (
    CompanyFactsRead,
    CompanyRead,
    FinancialFactRead,
    HistoricalTrendsRead,
    InsiderTransactionRead,
    InstitutionalHoldingRead,
)
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
        cik10 = client.get_company_cik(ticker)
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
        cik10 = client.get_company_cik(ticker)
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
        cik10 = client.get_company_cik(ticker)
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


@router.get("/{ticker}/historical-trends", response_model=HistoricalTrendsRead)
def get_historical_trends(
    ticker: str,
    fiscal_period: str = "FY",
    num_years: int = 6,
    num_forecast_years: int = 5,
    session: Session = Depends(get_session),
    client=Depends(get_sec_client),
) -> HistoricalTrendsRead:
    """Multi-year REPORTED historical series + deterministic CAGR / operating
    margin trend / SUGGESTED forward-growth projection for `ticker`.

    Pure deterministic computation over already-fetched SEC XBRL company
    facts (`SECConnector.get_company_facts` returns full multi-year history
    in one call) -- no AI, no interpolation, no fabricated data points. Every
    historical figure keeps its real REPORTED/MISSING `data_status`; the
    forward projection is unambiguously labeled `SUGGESTED` (see
    `backend/data/historical_analysis.py`) and is never written into any
    DCF/valuation input automatically.
    """
    try:
        cik10 = client.get_company_cik(ticker)
        company_facts = client.get_company_facts(cik10)
    except SECNotFoundError:
        raise HTTPException(status_code=404, detail=f"Unknown ticker or no XBRL facts available: {ticker}")
    except SECRateLimitError:
        raise HTTPException(status_code=503, detail="SEC EDGAR rate limit exceeded, please retry shortly")
    except SECUnavailableError:
        raise HTTPException(status_code=503, detail="SEC EDGAR is currently unavailable, please retry shortly")
    except SECError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    company = get_or_create_company(session, ticker=ticker, cik10=cik10)

    result = build_historical_trends(
        company_id=company.id,
        ticker=ticker,
        company_facts=company_facts,
        fiscal_period=fiscal_period,
        cik10=cik10,
        num_years=num_years,
        num_forecast_years=num_forecast_years,
    )

    if not result["fiscal_years_covered"]:
        raise HTTPException(status_code=404, detail=f"No {fiscal_period} historical XBRL facts available for {ticker}")

    # Persist the extracted multi-year facts so they're available like any
    # other FinancialFact row (same natural-key upsert as /facts).
    upsert_financial_facts(session, company.id, result.pop("facts"))

    return HistoricalTrendsRead(
        ticker=ticker.upper(),
        cik=cik10,
        company_id=company.id,
        fiscal_period=result["fiscal_period"],
        fiscal_years_covered=result["fiscal_years_covered"],
        series=result["series"],
        revenue_cagr=result["revenue_cagr"],
        net_income_cagr=result["net_income_cagr"],
        operating_margin_trend=result["operating_margin_trend"],
        suggested_forward_revenue=result["suggested_forward_revenue"],
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
        cik10 = client.get_company_cik(ticker)
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


@router.get("/{ticker}/insider-transactions")
def get_insider_transactions(
    ticker: str,
    limit: int = 20,
    session: Session = Depends(get_session),
    client=Depends(get_sec_client),
) -> dict:
    """Real Form 3/4/5 insider transaction history for `ticker` (as issuer).

    Pulls the company's most recent Form 3/4/5 filings (bounded by `limit`
    -- each filing requires its own XML fetch, so this caps the request
    fan-out), parses each ownership document
    (`SECConnector.get_ownership_document`), and persists every transaction
    row with full provenance. Fields genuinely absent from a given filing's
    XML come back `data_status=MISSING`, never fabricated.
    """
    try:
        cik10 = client.get_company_cik(ticker)
        filings = client.get_insider_filings(cik10)
    except SECNotFoundError:
        raise HTTPException(status_code=404, detail=f"Unknown ticker: {ticker}")
    except SECRateLimitError:
        raise HTTPException(status_code=503, detail="SEC EDGAR rate limit exceeded, please retry shortly")
    except SECUnavailableError:
        raise HTTPException(status_code=503, detail="SEC EDGAR is currently unavailable, please retry shortly")
    except SECError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    if not filings:
        raise HTTPException(status_code=404, detail=f"No Form 3/4/5 filings found for {ticker}")

    company = get_or_create_company(session, ticker=ticker, cik10=cik10)

    rows: list[InsiderTransaction] = []
    for filing in filings[:limit]:
        try:
            doc = client.get_ownership_document(cik10, filing["accession_number"])
        except SECError:
            # A single malformed/unreachable filing shouldn't sink the whole list.
            continue
        filing_date = _parse_date(filing.get("filing_date"))
        for txn in doc.transactions:
            rows.append(
                InsiderTransaction(
                    company_id=company.id,
                    reporting_owner_name=doc.reporting_owner_name,
                    reporting_owner_cik=doc.reporting_owner_cik,
                    is_officer=doc.is_officer,
                    is_director=doc.is_director,
                    is_ten_percent_owner=doc.is_ten_percent_owner,
                    is_other=doc.is_other,
                    officer_title=doc.officer_title,
                    transaction_table=txn.table,
                    security_title=txn.security_title,
                    transaction_date=_parse_date(txn.transaction_date),
                    transaction_code=txn.transaction_code,
                    shares_transacted=txn.transaction_shares,
                    price_per_share=txn.transaction_price_per_share,
                    transaction_acquired_disposed_code=txn.transaction_acquired_disposed_code,
                    shares_owned_after=txn.shares_owned_following_transaction,
                    ownership_type=txn.ownership_type,
                    accession_number=filing["accession_number"],
                    source="SEC_EDGAR_OWNERSHIP_XML",
                    source_url=filing["source_url"],
                    filing_date=filing_date,
                    data_status=DataStatus.REPORTED if txn.security_title else DataStatus.MISSING,
                )
            )

    persisted = upsert_insider_transactions(session, company.id, rows)
    persisted.sort(key=lambda t: t.transaction_date or date.min, reverse=True)

    return {
        "ticker": ticker.upper(),
        "cik": cik10,
        "company_id": company.id,
        "transaction_count": len(persisted),
        "transactions": [_insider_to_read(t) for t in persisted],
    }


@router.get("/{ticker}/institutional-holdings")
def get_institutional_holdings(
    ticker: str,
    filer_cik: str,
    accession_number: Optional[str] = None,
    issuer_contains: Optional[str] = None,
    session: Session = Depends(get_session),
    client=Depends(get_sec_client),
) -> dict:
    """Institutional (13F-HR) holdings reported by a specific filer, optionally
    filtered to positions whose issuer name contains `issuer_contains`.

    IMPORTANT design constraint (see docs/data-model.md): 13F filings are
    filed BY institutional investment managers ABOUT their own holdings --
    SEC does not publish a reverse index ("which 13F filers hold ticker X").
    So this endpoint is filer-keyed, not issuer-keyed: `filer_cik` (a known
    institutional manager's CIK, e.g. Berkshire Hathaway's 0001067983) is
    required. `ticker` in the path is used only to resolve/persist the
    calling company's own `Company` row for consistency with the rest of
    this router -- it does NOT filter the filer's holdings by itself. Use
    `issuer_contains` (a case-insensitive substring match against
    `nameOfIssuer`, e.g. the company name for `ticker`) to look for that
    company within the filer's reported positions.
    """
    try:
        cik10 = client.get_company_cik(ticker)
        get_or_create_company(session, ticker=ticker, cik10=cik10)
    except SECNotFoundError:
        raise HTTPException(status_code=404, detail=f"Unknown ticker: {ticker}")
    except SECRateLimitError:
        raise HTTPException(status_code=503, detail="SEC EDGAR rate limit exceeded, please retry shortly")
    except SECUnavailableError:
        raise HTTPException(status_code=503, detail="SEC EDGAR is currently unavailable, please retry shortly")
    except SECError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    filer_cik10 = filer_cik.zfill(10)
    try:
        if accession_number is None:
            latest = client.get_latest_filings(filer_cik10, form_types=("13F-HR",))
            if not latest:
                raise HTTPException(status_code=404, detail=f"No 13F-HR filings found for filer CIK {filer_cik10}")
            filing_meta = latest[0]
        else:
            filing_meta = client.get_filing_metadata(filer_cik10, accession_number)
        cover_page, holdings = client.get_13f_holdings(filer_cik10, filing_meta["accession_number"])
    except SECNotFoundError:
        raise HTTPException(status_code=404, detail=f"No 13F-HR data found for filer CIK {filer_cik10}")
    except SECRateLimitError:
        raise HTTPException(status_code=503, detail="SEC EDGAR rate limit exceeded, please retry shortly")
    except SECUnavailableError:
        raise HTTPException(status_code=503, detail="SEC EDGAR is currently unavailable, please retry shortly")
    except SECError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    filing_date = _parse_date(filing_meta.get("filing_date"))
    period_of_report = _parse_date(cover_page.period_of_report, fmt="%m-%d-%Y") or _parse_date(
        filing_meta.get("period_of_report")
    )

    rows: list[InstitutionalHolding] = []
    for h in holdings:
        if issuer_contains and (not h.name_of_issuer or issuer_contains.lower() not in h.name_of_issuer.lower()):
            continue
        rows.append(
            InstitutionalHolding(
                filer_cik=filer_cik10,
                filer_name=cover_page.filing_manager_name,
                issuer_name=h.name_of_issuer,
                title_of_class=h.title_of_class,
                cusip=h.cusip,
                period_of_report=period_of_report,
                value=h.value,
                shares_or_principal_amount=h.shares_or_principal_amount,
                shares_or_principal_type=h.shares_or_principal_type,
                investment_discretion=h.investment_discretion,
                voting_authority_sole=h.voting_authority_sole,
                voting_authority_shared=h.voting_authority_shared,
                voting_authority_none=h.voting_authority_none,
                accession_number=filing_meta["accession_number"],
                source="SEC_EDGAR_13F_INFO_TABLE",
                source_url=filing_meta["source_url"],
                filing_date=filing_date,
                data_status=DataStatus.REPORTED if h.cusip else DataStatus.MISSING,
            )
        )

    persisted = upsert_institutional_holdings(session, rows)

    return {
        "ticker": ticker.upper(),
        "filer_cik": filer_cik10,
        "filer_name": cover_page.filing_manager_name,
        "accession_number": filing_meta["accession_number"],
        "period_of_report": period_of_report,
        "issuer_contains": issuer_contains,
        "holding_count": len(persisted),
        "holdings": [_holding_to_read(h) for h in persisted],
        "governance_note": (
            "13F data is filed by the institutional manager (filer_cik) about its own holdings, not by/for "
            "the issuer. SEC does not publish an issuer-to-filer reverse index, so there is no direct "
            "'who holds ticker X' lookup -- this endpoint returns one known filer's holdings, optionally "
            "narrowed with issuer_contains."
        ),
    }


def _parse_date(value: Optional[str], fmt: str = "%Y-%m-%d"):
    if not value:
        return None
    try:
        return datetime.strptime(value, fmt).date()
    except ValueError:
        return None


def _insider_to_read(t: InsiderTransaction) -> InsiderTransactionRead:
    return InsiderTransactionRead(
        reporting_owner_name=t.reporting_owner_name,
        reporting_owner_cik=t.reporting_owner_cik,
        is_officer=t.is_officer,
        is_director=t.is_director,
        is_ten_percent_owner=t.is_ten_percent_owner,
        is_other=t.is_other,
        officer_title=t.officer_title,
        transaction_table=t.transaction_table,
        security_title=t.security_title,
        transaction_date=t.transaction_date,
        transaction_code=t.transaction_code,
        shares_transacted=t.shares_transacted,
        price_per_share=t.price_per_share,
        transaction_acquired_disposed_code=t.transaction_acquired_disposed_code,
        shares_owned_after=t.shares_owned_after,
        ownership_type=t.ownership_type,
        accession_number=t.accession_number,
        source=t.source,
        source_url=t.source_url,
        filing_date=t.filing_date,
        data_status=t.data_status.value,
    )


def _holding_to_read(h: InstitutionalHolding) -> InstitutionalHoldingRead:
    return InstitutionalHoldingRead(
        filer_cik=h.filer_cik,
        filer_name=h.filer_name,
        issuer_name=h.issuer_name,
        title_of_class=h.title_of_class,
        cusip=h.cusip,
        period_of_report=h.period_of_report,
        value=h.value,
        shares_or_principal_amount=h.shares_or_principal_amount,
        shares_or_principal_type=h.shares_or_principal_type,
        investment_discretion=h.investment_discretion,
        voting_authority_sole=h.voting_authority_sole,
        voting_authority_shared=h.voting_authority_shared,
        voting_authority_none=h.voting_authority_none,
        accession_number=h.accession_number,
        source=h.source,
        source_url=h.source_url,
        filing_date=h.filing_date,
        data_status=h.data_status.value,
    )


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
