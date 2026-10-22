import uuid
from collections.abc import AsyncIterator

import httpx

from spm.api.middleware import MAX_BODY_BYTES


async def test_request_id_is_echoed_when_supplied(client: httpx.AsyncClient) -> None:
    response = await client.get("/healthz", headers={"X-Request-ID": "abc-123"})
    assert response.headers["x-request-id"] == "abc-123"


async def test_request_id_generated_is_uuid7_when_absent(client: httpx.AsyncClient) -> None:
    response = await client.get("/healthz")
    generated = uuid.UUID(response.headers["x-request-id"])
    assert generated.version == 7


async def test_request_id_replaced_when_unsafe(client: httpx.AsyncClient) -> None:
    response = await client.get("/healthz", headers={"X-Request-ID": "bad id\twith spaces"})
    assert uuid.UUID(response.headers["x-request-id"]).version == 7


async def test_request_and_correlation_id_bound_in_log_context(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/_test/context", headers={"X-Request-ID": "req-1"})
    assert response.json() == {"request_id": "req-1", "correlation_id": "req-1"}


async def test_body_over_limit_returns_413(client: httpx.AsyncClient) -> None:
    body = b'{"n": 1, "note": "' + b"x" * MAX_BODY_BYTES + b'"}'
    response = await client.post(
        "/_test/echo", content=body, headers={"content-type": "application/json"}
    )
    assert response.status_code == 413
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["code"] == "PAYLOAD_TOO_LARGE"


async def test_streamed_body_over_limit_returns_413(client: httpx.AsyncClient) -> None:
    async def chunks() -> AsyncIterator[bytes]:
        yield b'{"n": 1, "note": "'
        for _ in range(5):
            yield b"x" * 4096
        yield b'"}'

    response = await client.post(
        "/_test/echo", content=chunks(), headers={"content-type": "application/json"}
    )
    assert "content-length" not in response.request.headers
    assert response.status_code == 413
    assert response.json()["code"] == "PAYLOAD_TOO_LARGE"


async def test_body_at_limit_is_accepted(client: httpx.AsyncClient) -> None:
    prefix, suffix = b'{"n": 1, "note": "', b'"}'
    body = prefix + b"x" * (MAX_BODY_BYTES - len(prefix) - len(suffix)) + suffix
    assert len(body) == MAX_BODY_BYTES
    response = await client.post(
        "/_test/echo", content=body, headers={"content-type": "application/json"}
    )
    assert response.status_code == 200


async def test_server_timing_header_present(client: httpx.AsyncClient) -> None:
    response = await client.get("/healthz")
    assert response.headers["server-timing"].startswith("app;dur=")


async def test_cors_allows_configured_origin(client: httpx.AsyncClient) -> None:
    response = await client.options(
        "/healthz",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"},
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


async def test_cors_rejects_other_origin(client: httpx.AsyncClient) -> None:
    response = await client.get("/healthz", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in response.headers
