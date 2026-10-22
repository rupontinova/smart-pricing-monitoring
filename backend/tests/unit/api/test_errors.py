import io
import json
import logging
from datetime import UTC, datetime

import httpx
import structlog
from fastapi import FastAPI

from spm.core.errors import RateLimited
from tests.conftest import client_for


async def test_unknown_route_returns_404_problem(client: httpx.AsyncClient) -> None:
    response = await client.get("/no-such-route")
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["code"] == "NOT_FOUND"
    assert body["status"] == 404
    assert body["type"] == "https://spm.dev/errors/not-found"
    assert body["request_id"] == response.headers["x-request-id"]


async def test_wrong_method_returns_405_problem(client: httpx.AsyncClient) -> None:
    response = await client.delete("/healthz")
    assert response.status_code == 405
    assert response.json()["code"] == "METHOD_NOT_ALLOWED"
    assert "GET" in response.headers["allow"]


async def test_bare_exception_returns_internal_without_stack_trace(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/_test/boom")
    assert response.status_code == 500
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["code"] == "INTERNAL"
    assert body["request_id"] == response.headers["x-request-id"]
    assert "secret internal detail" not in response.text
    assert "Traceback" not in response.text
    assert set(body) == {"type", "title", "status", "code", "request_id"}


async def test_spm_error_maps_to_problem_with_extras(client: httpx.AsyncClient) -> None:
    response = await client.get("/_test/domain-error")
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "UNKNOWN_SHOP"
    assert body["title"] == "Unknown shop"
    assert body["detail"] == "shop-x is not in the catalog"
    assert body["shop_id"] == "shop-x"
    assert body["type"] == "https://spm.dev/errors/unknown-shop"


async def test_validation_error_lists_invalid_fields(client: httpx.AsyncClient) -> None:
    response = await client.post("/_test/echo", json={"n": "not-a-number"})
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert body["errors"][0]["loc"] == ["body", "n"]
    assert "body.n" in body["detail"]


async def test_malformed_json_is_validation_error(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/_test/echo", content=b"{not json", headers={"content-type": "application/json"}
    )
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


async def test_unhandled_error_log_has_request_id_and_no_locals(
    client: httpx.AsyncClient,
) -> None:
    (handler,) = [
        h
        for h in logging.getLogger().handlers
        if isinstance(h.formatter, structlog.stdlib.ProcessorFormatter)
    ]
    assert isinstance(handler, logging.StreamHandler)
    buffer = io.StringIO()
    handler.setStream(buffer)
    await client.get(
        "/_test/boom", headers={"X-Request-ID": "trace-me", "Authorization": "Bearer s3cret"}
    )
    lines = [json.loads(line) for line in buffer.getvalue().splitlines() if line.strip()]
    (error,) = [line for line in lines if line["event"] == "unhandled_error"]
    assert error["request_id"] == "trace-me"
    assert error["exception"][0]["exc_type"] == "Exception"
    assert "s3cret" not in json.dumps(error)


async def test_rate_limited_sets_retry_after(app: FastAPI) -> None:
    @app.get("/_test/limited")
    async def limited() -> None:
        raise RateLimited("Hourly report limit reached.", retry_after_s=42)

    async for c in client_for(app):
        response = await c.get("/_test/limited")
    assert response.status_code == 429
    assert response.headers["retry-after"] == "42"
    assert response.json()["code"] == "RATE_LIMITED"
    assert response.json()["retry_after_s"] == 42


async def test_problem_extras_are_json_encoded(app: FastAPI) -> None:
    from decimal import Decimal

    @app.get("/_test/decimal-extra")
    async def decimal_extra() -> None:
        raise RateLimited(retry_after_s=5, limit=Decimal("30"), at=datetime(2026, 1, 1, tzinfo=UTC))

    async for c in client_for(app):
        body = (await c.get("/_test/decimal-extra")).json()
    assert body["limit"] == 30
    assert body["at"] == "2026-01-01T00:00:00+00:00"
