"""Request body size limit (413) on the FastAPI app."""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from backend.api.body_limit import DEFAULT_MAX_BODY_BYTES, BodySizeLimitMiddleware, max_body_bytes
from backend.api.main import app
from backend.tests.fixtures.auth import auth_headers

client = TestClient(app, raise_server_exceptions=False)
URL = "/valuation/dcf"  # an authenticated POST route that parses a JSON body


def _post(content: bytes, headers=None):
    h = {"Content-Type": "application/json", **auth_headers(), **(headers or {})}
    return client.post(URL, content=content, headers=h)


def test_default_limit_is_one_mebibyte(monkeypatch):
    monkeypatch.delenv("MAX_REQUEST_BODY_BYTES", raising=False)
    assert DEFAULT_MAX_BODY_BYTES == 1024 * 1024 == max_body_bytes()


@pytest.mark.parametrize("raw", ["junk", "0", "-5", ""])
def test_a_bad_setting_falls_back_to_the_default(monkeypatch, raw):
    monkeypatch.setenv("MAX_REQUEST_BODY_BYTES", raw)
    assert max_body_bytes() == DEFAULT_MAX_BODY_BYTES


def test_oversized_body_is_413_with_a_json_error(monkeypatch):
    monkeypatch.setenv("MAX_REQUEST_BODY_BYTES", "200")

    resp = _post(b'{"pad": "' + b"x" * 500 + b'"}')

    assert resp.status_code == 413
    assert resp.headers["content-type"] == "application/json"
    assert "too large" in resp.json()["detail"].lower() and "200" in resp.json()["detail"]


def test_one_mebibyte_default_rejects_a_1_5_mebibyte_body(monkeypatch):
    monkeypatch.delenv("MAX_REQUEST_BODY_BYTES", raising=False)
    assert _post(b'{"pad": "' + b"x" * (1_500_000) + b'"}').status_code == 413


def test_a_body_exactly_at_the_limit_gets_through_and_one_byte_more_does_not(monkeypatch):
    monkeypatch.setenv("MAX_REQUEST_BODY_BYTES", "100")
    at_limit = b'{"pad": "' + b"x" * (100 - len(b'{"pad": ""}')) + b'"}'
    assert len(at_limit) == 100

    assert _post(at_limit).status_code == 422  # reached the handler (invalid DCF input), not rejected
    assert _post(at_limit + b" ").status_code == 413


def test_the_handler_never_runs_for_an_oversized_request(monkeypatch):
    monkeypatch.setenv("MAX_REQUEST_BODY_BYTES", "50")
    # With a valid token the only thing between us and the handler is the limit; an
    # unauthenticated oversized request is also 413 (the limit runs before auth).
    resp = client.post(URL, content=b"x" * 500, headers={"Content-Type": "application/json"})
    assert resp.status_code == 413


def test_chunked_upload_without_content_length_is_still_limited(monkeypatch):
    monkeypatch.setenv("MAX_REQUEST_BODY_BYTES", "1000")

    def chunks():
        for _ in range(50):
            yield b"x" * 100  # 5000 bytes total, no Content-Length header

    resp = client.post(URL, content=chunks(), headers={"Content-Type": "application/json", **auth_headers()})

    assert resp.status_code == 413


def test_a_lying_content_length_is_caught_by_counting_the_real_bytes():
    sent: list[dict] = []
    reached_app = False

    async def inner(scope, receive, send):
        nonlocal reached_app
        reached_app = True
        while True:  # a handler that reads the whole body
            message = await receive()
            if message["type"] == "http.disconnect" or not message.get("more_body"):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    stream = [
        {"type": "http.request", "body": b"x" * 60, "more_body": True},
        {"type": "http.request", "body": b"x" * 60, "more_body": False},
    ]

    async def receive():
        return stream.pop(0)

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "headers": [(b"content-length", b"10")]}  # claims 10 bytes, sends 120
    asyncio.run(BodySizeLimitMiddleware(inner, max_bytes=100)(scope, receive, send))

    assert reached_app
    assert sent[0]["status"] == 413
    assert json.loads(sent[1]["body"])["detail"].startswith("Request body too large")
    assert len(sent) == 2  # the app's own "200 ok" after the cut-off was dropped


def test_a_streaming_handler_that_already_responded_is_not_corrupted():
    """If the response has started, a later oversize read cannot be turned into a second response."""
    sent: list[dict] = []

    async def inner(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await receive()  # trips the limit after the response began

    async def receive():
        return {"type": "http.request", "body": b"x" * 500, "more_body": False}

    async def send(message):
        sent.append(message)

    asyncio.run(BodySizeLimitMiddleware(inner, max_bytes=100)({"type": "http", "headers": []}, receive, send))

    assert [m["type"] for m in sent] == ["http.response.start"]  # no second start, no 413


def test_requests_without_a_body_and_websocket_scopes_are_untouched(monkeypatch):
    monkeypatch.setenv("MAX_REQUEST_BODY_BYTES", "10")
    assert client.get("/health").status_code == 200

    called = []

    async def inner(scope, receive, send):
        called.append(scope["type"])

    asyncio.run(BodySizeLimitMiddleware(inner, max_bytes=1)({"type": "websocket"}, None, None))
    assert called == ["websocket"]


def test_413_carries_cors_headers_for_an_allowed_origin(monkeypatch):
    monkeypatch.setenv("MAX_REQUEST_BODY_BYTES", "50")
    resp = _post(b"x" * 500, headers={"Origin": "http://localhost:3000"})
    assert resp.status_code == 413
    assert resp.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_login_body_is_limited_too(monkeypatch):
    monkeypatch.setenv("MAX_REQUEST_BODY_BYTES", "300")
    resp = client.post("/auth/login", json={"email": "a@b.co", "password": "p" * 1000})
    assert resp.status_code == 413
