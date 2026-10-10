"""CORS: explicit methods and headers, and a refusal to start with an unsafe origin list."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.api.main import (
    CORS_ALLOW_CREDENTIALS,
    CORS_ALLOW_HEADERS,
    CORS_ALLOW_METHODS,
    app,
    origins,
    parse_allowed_origins,
)

client = TestClient(app, raise_server_exceptions=False)
ORIGIN = "http://localhost:3000"
REPO = Path(__file__).resolve().parents[2]


def _preflight(*, method="POST", headers="authorization,content-type", origin=ORIGIN):
    return client.options(
        "/valuation/dcf",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": headers,
        },
    )


# --------------------------------------------------------------------------
# parse_allowed_origins
# --------------------------------------------------------------------------


@pytest.mark.parametrize("raw", ["*", "http://localhost:3000,*", " * ", "https://*.example.com", "http://*"])
def test_wildcards_are_refused_because_credentials_are_allowed(raw):
    assert CORS_ALLOW_CREDENTIALS is True
    with pytest.raises(RuntimeError, match="wildcard"):
        parse_allowed_origins(raw)


def test_a_bare_wildcard_is_only_tolerated_when_credentials_are_off():
    assert parse_allowed_origins("*", allow_credentials=False) == ["*"]
    with pytest.raises(RuntimeError, match="wildcard"):
        parse_allowed_origins("*", allow_credentials=True)
    with pytest.raises(RuntimeError, match="not an origin"):  # a partial wildcard is never an origin
        parse_allowed_origins("https://*.example.com", allow_credentials=False)


@pytest.mark.parametrize("raw", ["localhost:3000", "ftp://x.com", "http://x.com/app", "null", "app.example.com"])
def test_entries_must_be_scheme_host_port_origins(raw):
    with pytest.raises(RuntimeError, match="not an origin"):
        parse_allowed_origins(raw)


def test_good_lists_parse_and_whitespace_and_blanks_are_ignored():
    assert parse_allowed_origins("http://localhost:3000, https://app.example.com ,,") == [
        "http://localhost:3000",
        "https://app.example.com",
    ]
    assert parse_allowed_origins("") == []


def test_the_apps_actual_origin_list_has_no_wildcard():
    assert origins and "*" not in "".join(origins)


def _import_main(allowed: str | None):
    env = {k: v for k, v in os.environ.items() if k != "ALLOWED_ORIGINS"}
    if allowed is not None:
        env["ALLOWED_ORIGINS"] = allowed
    return subprocess.run(
        [sys.executable, "-c", "import sys; sys.path.insert(0, %r); import backend.api.main" % str(REPO)],
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO,
    )


def test_startup_fails_when_allowed_origins_has_a_wildcard():
    out = _import_main("*")
    assert out.returncode != 0
    assert "wildcard" in out.stderr and "credentials" in out.stderr


def test_startup_fails_on_a_wildcard_mixed_into_a_real_list():
    assert _import_main("http://localhost:3000,https://*.evil.example").returncode != 0


def test_startup_succeeds_with_the_default_and_with_explicit_origins():
    assert _import_main(None).returncode == 0
    assert _import_main("https://app.example.com,http://localhost:3000").returncode == 0


# --------------------------------------------------------------------------
# what the middleware actually answers
# --------------------------------------------------------------------------


def test_methods_and_headers_are_explicit_lists_not_wildcards():
    assert "*" not in CORS_ALLOW_METHODS and "*" not in CORS_ALLOW_HEADERS
    assert set(CORS_ALLOW_METHODS) == {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}
    assert set(CORS_ALLOW_HEADERS) == {"Authorization", "Content-Type", "Accept"}


def test_preflight_for_an_allowed_origin_lists_exactly_the_allowed_methods_and_headers():
    resp = _preflight()

    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == ORIGIN
    assert resp.headers["access-control-allow-credentials"] == "true"
    assert set(m.strip() for m in resp.headers["access-control-allow-methods"].split(",")) == set(CORS_ALLOW_METHODS)
    allowed_headers = {h.strip().lower() for h in resp.headers["access-control-allow-headers"].split(",")}
    ours = {h.lower() for h in CORS_ALLOW_HEADERS}
    # Starlette always adds the CORS-safelisted request headers; nothing else may appear.
    assert ours <= allowed_headers <= ours | {"accept-language", "content-language"}
    assert "*" not in resp.headers["access-control-allow-methods"] + resp.headers["access-control-allow-headers"]


@pytest.mark.parametrize("method", ["TRACE", "CONNECT", "PROPFIND"])
def test_preflight_for_a_method_we_do_not_allow_is_refused(method):
    resp = _preflight(method=method)
    assert resp.status_code == 400  # a non-2xx preflight makes the browser block the real request
    assert "disallowed cors method" in resp.text.lower()


@pytest.mark.parametrize("header", ["x-evil", "authorization,x-api-key", "cookie"])
def test_preflight_requesting_a_header_we_do_not_allow_is_refused(header):
    resp = _preflight(headers=header)
    assert resp.status_code == 400
    assert "disallowed cors headers" in resp.text.lower()


def test_preflight_from_an_origin_that_is_not_listed_gets_no_permission():
    resp = _preflight(origin="https://evil.example")
    assert resp.status_code == 400
    assert "disallowed cors origin" in resp.text.lower()
    assert "access-control-allow-origin" not in {k.lower() for k in resp.headers}


def test_a_normal_response_for_an_unlisted_origin_has_no_allow_origin_header():
    resp = client.get("/health", headers={"Origin": "https://evil.example"})
    assert resp.status_code == 200
    assert "access-control-allow-origin" not in {k.lower() for k in resp.headers}


def test_a_normal_response_for_a_listed_origin_allows_credentials():
    resp = client.get("/health", headers={"Origin": ORIGIN})
    assert resp.headers["access-control-allow-origin"] == ORIGIN
    assert resp.headers["access-control-allow-credentials"] == "true"
