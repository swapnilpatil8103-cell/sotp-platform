"""Request body size limit (413), as pure ASGI middleware.

Two layers, because ``Content-Length`` can be absent (chunked uploads) or lie:

1. If ``Content-Length`` exceeds the limit, answer 413 immediately without
   reading anything.
2. Otherwise count the bytes actually received; the moment the running total
   passes the limit, answer 413 ourselves and tell the application the client
   disconnected. (Raising from ``receive()`` would not work: FastAPI catches any
   exception while parsing a body and reports it as a 400.) Anything the
   application tries to send afterwards is dropped.

The limit comes from ``MAX_REQUEST_BODY_BYTES`` (default 1 MiB). It guards the
whole API; no endpoint here legitimately takes a large upload.
"""

from __future__ import annotations

import json
import os

DEFAULT_MAX_BODY_BYTES = 1024 * 1024


def max_body_bytes() -> int:
    try:
        value = int(os.environ.get("MAX_REQUEST_BODY_BYTES", DEFAULT_MAX_BODY_BYTES))
    except ValueError:
        return DEFAULT_MAX_BODY_BYTES
    return value if value > 0 else DEFAULT_MAX_BODY_BYTES


class BodySizeLimitMiddleware:
    def __init__(self, app, max_bytes: int | None = None):
        self.app = app
        self._max_bytes = max_bytes

    @property
    def max_bytes(self) -> int:
        return self._max_bytes if self._max_bytes is not None else max_body_bytes()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        limit = self.max_bytes
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    declared = 0
                if declared > limit:
                    await self._reject(send, limit)
                    return
                break

        received = 0
        response_started = False  # the application has begun its own response
        cut = False  # the body went over the limit; stop feeding the application
        rejected = False  # we sent the 413, so drop whatever the application sends

        async def counting_receive():
            nonlocal received, cut, rejected
            if cut:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    cut = True
                    if not response_started:  # if the app already answered, all we can do is cut the read
                        rejected = True
                        await self._reject(send, limit)
                    return {"type": "http.disconnect"}
            return message

        async def tracking_send(message):
            nonlocal response_started
            if rejected:
                return
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        await self.app(scope, counting_receive, tracking_send)

    @staticmethod
    async def _reject(send, limit: int) -> None:
        body = json.dumps({"detail": f"Request body too large (limit {limit} bytes)."}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    (b"connection", b"close"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
