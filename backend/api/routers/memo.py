"""Memo router — assembles a real investment memo from persisted data.

GET /memo/{ticker} pulls whatever has actually been persisted for the
company (Company row, latest FinancialFacts, Segments + coverage, and the
most recent ValuationRun per method) and feeds that structured data into
backend.ai.tasks.memo_generator's generate_memo_section, section by
section. It never invents numbers: if no ValuationRun exists yet for the
company (i.e. no completed, human-governed valuation), this 404s with a
clear "insufficient data" message instead of fabricating a memo.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from backend.ai.adapter import AIAdapter
from backend.ai.gemini_adapter import GeminiAdapter
from backend.ai.tasks.memo_generator import MemoSectionResult, generate_memo_section
from backend.api.deps import get_sec_client
from backend.data.persistence import get_or_create_company
from backend.db import get_session
from backend.models.company import Company
from backend.models.financial_fact import FinancialFact
from backend.models.segment import Segment
from backend.models.segment_financial_fact import SegmentFinancialFact
from backend.models.valuation_run import ValuationRun
from backend.services.sec_client import SECError, SECNotFoundError, SECRateLimitError, SECUnavailableError

router = APIRouter(prefix="/memo", tags=["memo"])

MEMO_SECTIONS = (
    "Executive Summary",
    "Company Overview",
    "Segment Analysis",
    "Valuation Methodology",
    "SOTP Breakdown",
    "Risks & Uncertainties",
)


def _get_adapter() -> AIAdapter:
    return GeminiAdapter()


class MemoResponse(BaseModel):
    ticker: str
    company_id: int
    company_name: str
    insufficient_data: bool = False
    reason: Optional[str] = None
    data_snapshot: dict[str, Any] = {}
    sections: list[MemoSectionResult] = []


def _latest_facts(session: Session, company_id: int) -> dict[str, Any]:
    facts = session.exec(
        select(FinancialFact).where(FinancialFact.company_id == company_id)
    ).all()
    if not facts:
        return {}
    latest_year = max(f.fiscal_year for f in facts)
    latest = [f for f in facts if f.fiscal_year == latest_year and f.fiscal_period == "FY"]
    if not latest:
        latest = [f for f in facts if f.fiscal_year == latest_year]
    return {
        "fiscal_year": latest_year,
        "facts": {
            f.concept: f.value
            for f in latest
            if f.value is not None
        },
    }


def _segment_snapshot(session: Session, company_id: int) -> list[dict[str, Any]]:
    segments = session.exec(select(Segment).where(Segment.company_id == company_id)).all()
    out = []
    for seg in segments:
        seg_facts = session.exec(
            select(SegmentFinancialFact).where(SegmentFinancialFact.segment_id == seg.id)
        ).all()
        if not seg_facts:
            continue
        out.append(
            {
                "name": seg.name,
                "facts": {f.concept: f.value for f in seg_facts if f.value is not None},
            }
        )
    return out


def _latest_valuation_runs(session: Session, company_id: int) -> dict[str, Any]:
    runs = session.exec(
        select(ValuationRun).where(ValuationRun.company_id == company_id)
    ).all()
    latest_by_method: dict[str, ValuationRun] = {}
    for run in runs:
        method = run.method.value if hasattr(run.method, "value") else str(run.method)
        current = latest_by_method.get(method)
        if current is None or run.version > current.version:
            latest_by_method[method] = run
    return {
        method: {
            "version": run.version,
            "outputs": run.outputs,
        }
        for method, run in latest_by_method.items()
    }


@router.get("/{ticker}", response_model=MemoResponse)
def get_memo(
    ticker: str,
    session: Session = Depends(get_session),
    adapter: AIAdapter = Depends(_get_adapter),
    client=Depends(get_sec_client),
) -> MemoResponse:
    # Resolve ticker -> Company the same way /companies/{ticker} does, so a
    # memo can be requested for any company that's ever had a ticker
    # resolved -- but a Company row alone (no valuation run) is still
    # "insufficient data" below.
    try:
        cik10 = client.get_company_cik(ticker)
    except SECNotFoundError:
        raise HTTPException(status_code=404, detail=f"Unknown ticker: {ticker}")
    except SECRateLimitError:
        raise HTTPException(status_code=503, detail="SEC EDGAR rate limit exceeded, please retry shortly")
    except SECUnavailableError:
        raise HTTPException(status_code=503, detail="SEC EDGAR is currently unavailable, please retry shortly")
    except SECError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    company = session.exec(select(Company).where(Company.ticker == ticker.upper())).first()
    if company is None:
        company = get_or_create_company(session, ticker=ticker, cik10=cik10)

    valuation_runs = _latest_valuation_runs(session, company.id)
    if not valuation_runs:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Insufficient data to generate a memo for {ticker.upper()}: no completed "
                "valuation run exists yet. Run and approve a valuation "
                "(POST /valuation/run) before requesting a memo."
            ),
        )

    facts_snapshot = _latest_facts(session, company.id)
    segments_snapshot = _segment_snapshot(session, company.id)

    memo_data: dict[str, Any] = {
        "ticker": company.ticker,
        "company_name": company.name,
        "sector": company.sector,
        "industry": company.industry,
        **facts_snapshot,
        "segments": segments_snapshot,
        "valuation_runs": valuation_runs,
    }

    sections = [generate_memo_section(adapter, section, memo_data) for section in MEMO_SECTIONS]

    return MemoResponse(
        ticker=company.ticker,
        company_id=company.id,
        company_name=company.name,
        insufficient_data=False,
        data_snapshot=memo_data,
        sections=sections,
    )
