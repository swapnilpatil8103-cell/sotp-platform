"""
SOTP Intelligence FastAPI application entrypoint.

Run with: uvicorn backend.api.main:app --reload
"""

import os

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.deps import get_current_user
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


# Every router requires a valid Bearer token. Applying the dependency here, at
# include time, protects every current and future route in these routers by
# default; /health above is the only open endpoint. Endpoints that record a
# decision or execute a run add a role check on top (see valuation.py).
_AUTH = [Depends(get_current_user)]

app.include_router(companies.router, dependencies=_AUTH)
app.include_router(market_data.router, dependencies=_AUTH)
app.include_router(filings.router, dependencies=_AUTH)
app.include_router(segments.router, dependencies=_AUTH)
app.include_router(valuation.router, dependencies=_AUTH)
app.include_router(scenarios.router, dependencies=_AUTH)
app.include_router(memo.router, dependencies=_AUTH)
