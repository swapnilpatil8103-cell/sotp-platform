"""Filings router — thin wrapper delegating to the SEC-backed company filings lookup.

Kept as a top-level `/filings` collection per the original API shape; the
actual SEC lookup logic lives in `backend.api.routers.companies.get_company_filings`
to avoid duplicating SECClient error handling.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from backend.api.routers.companies import get_company_filings

router = APIRouter(prefix="/filings", tags=["filings"])


@router.get("")
def list_filings(ticker: str | None = Query(default=None)):
    if not ticker:
        raise HTTPException(status_code=400, detail="Query param 'ticker' is required")
    return get_company_filings(ticker)
