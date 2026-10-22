"""Pure ASGI middleware: request ids, body size limit, Server-Timing, unhandled errors."""

import re
import time

import structlog
from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from spm.adapters.ids import new_uuid7
from spm.api.errors import internal_problem, problem

MAX_BODY_BYTES = 16 * 1024
REQUEST_ID_HEADER = "X-Request-ID"
# Accept a caller's id only if it is short and log-safe; otherwise generate our own.
_VALID_REQUEST_ID = re.compile(r"[A-Za-z0-9._:\-]{1,128}")

log = structlog.get_logger("spm.api")


class RequestIdMiddleware:
    """Reads ``X-Request-ID`` (or generates a UUIDv7), binds it to logs and echoes it back."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = Headers(scope=scope).get(REQUEST_ID_HEADER)
        valid = incoming is not None and _VALID_REQUEST_ID.fullmatch(incoming)
        request_id = incoming if valid and incoming else str(new_uuid7())
        scope.setdefault("state", {})["request_id"] = request_id
        status = 500
        started = time.perf_counter()

        async def send_with_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        with structlog.contextvars.bound_contextvars(
            request_id=request_id, correlation_id=request_id
        ):
            try:
                await self.app(scope, receive, send_with_id)
            finally:
                log.info(
                    "request",
                    method=scope["method"],
                    path=scope["path"],
                    status=status,
                    duration_ms=round((time.perf_counter() - started) * 1000, 1),
                )


class ServerTimingMiddleware:
    """Adds ``Server-Timing: app;dur=<ms>`` (time until the response headers are sent)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()

        async def send_with_timing(message: Message) -> None:
            if message["type"] == "http.response.start":
                duration_ms = (time.perf_counter() - started) * 1000
                MutableHeaders(scope=message).append("Server-Timing", f"app;dur={duration_ms:.1f}")
            await send(message)

        await self.app(scope, receive, send_with_timing)


class BodySizeLimitMiddleware:
    """Rejects request bodies over ``max_bytes`` with a 413 problem.

    A declared ``Content-Length`` is checked up front; chunked bodies are counted as they stream in.
    """

    def __init__(self, app: ASGIApp, max_bytes: int = MAX_BODY_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        detail = f"Request body exceeds the limit of {self.max_bytes} bytes."
        declared = Headers(scope=scope).get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > self.max_bytes:
            response = problem(413, "PAYLOAD_TOO_LARGE", "Request body too large", detail)
            await response(scope, receive, send)
            return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    # Re-raised by FastAPI's body parsing and rendered by the HTTP error handler.
                    raise StarletteHTTPException(413, detail)
            return message

        await self.app(scope, limited_receive, send)


class UnhandledErrorMiddleware:
    """Turns any unhandled exception into an ``INTERNAL`` problem; the traceback is only logged."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        response_started = False

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive, tracking_send)
        except Exception:
            log.exception("unhandled_error", method=scope["method"], path=scope["path"])
            if response_started:
                raise
            await internal_problem()(scope, receive, send)
