"""
SOTP Intelligence FastAPI application entrypoint.

Run with: uvicorn backend.api.main:app --reload
"""

import math
import os
from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.api.deps import get_current_user
from backend.api.routers import auth, companies, filings, market_data, segments, valuation, scenarios, memo

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


def _json_safe(value: Any) -> Any:
    """Replace NaN/Infinity (which JSON cannot represent) with their string form."""
    if isinstance(value, float) and not math.isfinite(value):
        return repr(value)
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


@app.exception_handler(RequestValidationError)
async def _validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Same 422 body FastAPI produces by default, except non-finite floats are
    stringified. A validation error echoes the offending input back; for a body
    like {"wacc": NaN} that input is a float JSON cannot encode, so the default
    handler crashed while building the 422 and the client got a 500."""
    return JSONResponse(status_code=422, content={"detail": _json_safe(jsonable_encoder(exc.errors()))})


@app.get("/health", tags=["health"])
def health():
    """Basic liveness check."""
    return {"status": "ok"}


# Every router requires a valid Bearer token. Applying the dependency here, at
# include time, protects every current and future route in these routers by
# default; /health above and /auth/login are the only open endpoints. Endpoints that record a
# decision or execute a run add a role check on top (see valuation.py).
_AUTH = [Depends(get_current_user)]

# The one other open route: exchanging credentials for a token.
app.include_router(auth.router)
app.include_router(companies.router, dependencies=_AUTH)
app.include_router(market_data.router, dependencies=_AUTH)
app.include_router(filings.router, dependencies=_AUTH)
app.include_router(segments.router, dependencies=_AUTH)
app.include_router(valuation.router, dependencies=_AUTH)
app.include_router(scenarios.router, dependencies=_AUTH)
app.include_router(memo.router, dependencies=_AUTH)
