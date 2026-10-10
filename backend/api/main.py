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

from backend.api.body_limit import BodySizeLimitMiddleware
from backend.api.deps import get_current_user
from backend.api.routers import admin, auth, companies, filings, market_data, segments, valuation, scenarios, memo

app = FastAPI(
    title="SOTP Intelligence API",
    description="SEC-data-driven company valuation platform (Phase 1 foundation).",
    version="0.1.0",
)

# Explicit, not "*": the API only needs these verbs and request headers.
CORS_ALLOW_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
CORS_ALLOW_HEADERS = ["Authorization", "Content-Type", "Accept"]
CORS_ALLOW_CREDENTIALS = True


def parse_allowed_origins(raw: str, *, allow_credentials: bool = CORS_ALLOW_CREDENTIALS) -> list[str]:
    """Parse ALLOWED_ORIGINS, refusing configurations that are unsafe.

    A wildcard origin combined with credentials would let any website make
    authenticated requests, so it aborts startup instead of being "handled". Every
    entry must be an exact http(s) origin (scheme://host[:port], no path).
    """
    origins = [o.strip() for o in raw.split(",") if o.strip()]
    for origin in origins:
        if "*" in origin and allow_credentials:
            raise RuntimeError(
                f"ALLOWED_ORIGINS contains a wildcard ({origin!r}) while credentials are allowed. "
                "List explicit origins, e.g. ALLOWED_ORIGINS=https://app.example.com"
            )
        if origin == "*":  # only reachable when credentials are off
            continue
        # Partial wildcards (https://*.example.com) are not supported by the CORS
        # middleware: they would silently match nothing, so refuse them outright.
        if "*" in origin or not origin.startswith(("http://", "https://")) or "/" in origin.split("://", 1)[1]:
            raise RuntimeError(
                f"ALLOWED_ORIGINS entry {origin!r} is not an origin of the form scheme://host[:port]."
            )
    return origins


origins = parse_allowed_origins(os.environ.get("ALLOWED_ORIGINS", "http://localhost:3000"))

# Added first = innermost. CORS is added last so it is outermost and a 413 from
# the body limit still carries CORS headers.
app.add_middleware(BodySizeLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=CORS_ALLOW_CREDENTIALS,
    allow_methods=CORS_ALLOW_METHODS,
    allow_headers=CORS_ALLOW_HEADERS,
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
app.include_router(admin.router, dependencies=_AUTH)
app.include_router(companies.router, dependencies=_AUTH)
app.include_router(market_data.router, dependencies=_AUTH)
app.include_router(filings.router, dependencies=_AUTH)
app.include_router(segments.router, dependencies=_AUTH)
app.include_router(valuation.router, dependencies=_AUTH)
app.include_router(scenarios.router, dependencies=_AUTH)
app.include_router(memo.router, dependencies=_AUTH)
