"""
SOTP Intelligence FastAPI application entrypoint.

Run with: uvicorn backend.api.main:app --reload
"""

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.routers import companies, filings, market_data, segments, valuation, scenarios, memo

app = FastAPI(
    title="SOTP Intelligence API",
    description="SEC-data-driven company valuation platform (Phase 1 foundation).",
    version="0.1.0",
)

_allowed_origins = os.environ.get("ALLOWED_ORIGINS", "http://localhost:3000")
origins = [o.strip() for o in _allowed_origins.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", tags=["health"])
def health():
    """Basic liveness check."""
    return {"status": "ok"}


app.include_router(companies.router)
app.include_router(market_data.router)
app.include_router(filings.router)
app.include_router(segments.router)
app.include_router(valuation.router)
app.include_router(scenarios.router)
app.include_router(memo.router)
