import httpx

from spm import __version__
from spm.api.app import create_app
from tests.conftest import client_for, make_settings


async def test_healthz_returns_ok_with_version(client: httpx.AsyncClient) -> None:
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


async def test_readyz_ok_when_no_dependency_configured(client: httpx.AsyncClient) -> None:
    response = await client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"db": "skipped", "redis": "skipped"}}


async def test_readyz_returns_503_when_redis_configured_but_unreachable() -> None:
    app = create_app(make_settings(redis_url="redis://127.0.0.1:1/0"))
    async for client in client_for(app):
        response = await client.get("/readyz")
    assert response.status_code == 503
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["code"] == "SERVICE_UNAVAILABLE"
    assert body["checks"] == {"db": "skipped", "redis": "error"}
    assert body["request_id"]


async def test_readyz_returns_503_when_db_configured_but_unreachable() -> None:
    app = create_app(make_settings(database_url="postgresql+asyncpg://spm:spm@127.0.0.1:1/spm"))
    async for client in client_for(app):
        response = await client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["checks"]["db"] == "error"


async def test_docs_served(client: httpx.AsyncClient) -> None:
    assert (await client.get("/docs")).status_code == 200
    assert (await client.get("/openapi.json")).json()["info"]["version"] == __version__
