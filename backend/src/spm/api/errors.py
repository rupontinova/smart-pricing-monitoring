"""RFC 9457 problem details (``application/problem+json``) for every error (MODULES.md §2.5)."""

from collections.abc import Mapping
from http import HTTPStatus
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from spm.core.errors import ERROR_TYPES, Internal, RateLimited, SpmError, Unauthorized

PROBLEM_JSON = "application/problem+json"
ERROR_TYPE_BASE = "https://spm.dev/errors/"

# Registry code for each HTTP status, for errors raised by the framework rather than our code.
# 422 is VALIDATION_ERROR: the domain 422s (UNKNOWN_PRODUCT, …) are always raised as SpmError.
HTTP_STATUS_CODES: dict[int, str] = {
    cls.status: cls.code for cls in ERROR_TYPES if cls.status != 422
} | {422: "VALIDATION_ERROR"}


def current_request_id() -> str | None:
    request_id = structlog.contextvars.get_contextvars().get("request_id")
    return str(request_id) if request_id is not None else None


def problem(
    status: int,
    code: str,
    title: str | None = None,
    detail: str | None = None,
    *,
    headers: Mapping[str, str] | None = None,
    extra: Mapping[str, Any] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": ERROR_TYPE_BASE + code.lower().replace("_", "-"),
        "title": title or HTTPStatus(status).phrase,
        "status": status,
        "code": code,
    }
    if detail:
        body["detail"] = detail
    if extra:
        body.update(jsonable_encoder(extra))  # Decimals, datetimes, UUIDs in error extras
    body["request_id"] = current_request_id()
    return JSONResponse(body, status_code=status, headers=headers, media_type=PROBLEM_JSON)


def internal_problem() -> JSONResponse:
    """The only body ever returned for an unhandled error: no exception text, no stack trace."""
    return problem(Internal.status, Internal.code, Internal.title)


def code_for_status(status: int) -> str:
    if status in HTTP_STATUS_CODES:
        return HTTP_STATUS_CODES[status]
    return "INTERNAL" if status >= 500 else "BAD_REQUEST"


async def _spm_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, SpmError)
    headers: dict[str, str] | None = None
    if isinstance(exc, RateLimited):
        headers = {"Retry-After": str(exc.retry_after_s)}
    elif isinstance(exc, Unauthorized):
        headers = {"WWW-Authenticate": "Bearer"}
    return problem(
        exc.status, exc.code, exc.title, exc.detail or None, headers=headers, extra=exc.extra
    )


async def _validation_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    errors = [
        {"loc": list(err.get("loc", ())), "msg": err.get("msg", ""), "type": err.get("type", "")}
        for err in exc.errors()
    ]
    fields = ", ".join(".".join(str(part) for part in e["loc"]) for e in errors)
    return problem(
        422,
        "VALIDATION_ERROR",
        "Request validation failed",
        f"Invalid fields: {fields}" if fields else None,
        extra={"errors": errors},
    )


async def _http_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    phrase = HTTPStatus(exc.status_code).phrase
    detail = exc.detail if isinstance(exc.detail, str) and exc.detail != phrase else None
    return problem(
        exc.status_code,
        code_for_status(exc.status_code),
        phrase,
        detail,
        headers=dict(exc.headers) if exc.headers else None,
    )


def install_exception_handlers(app: FastAPI) -> None:
    # Unhandled exceptions are caught by UnhandledErrorMiddleware (see middleware.py), which
    # sits inside RequestIdMiddleware so the 500 still carries the request id.
    app.add_exception_handler(SpmError, _spm_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
