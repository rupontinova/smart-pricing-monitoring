from collections.abc import AsyncIterator

import httpx
import pytest
import structlog
from fastapi import FastAPI
from pydantic import BaseModel

from spm.api.app import create_app
from spm.config import Settings
from spm.core.errors import SpmError
from tests.conftest import client_for


class Payload(BaseModel):
    n: int
    note: str = ""


class _Teapot(SpmError):
    code = "UNKNOWN_SHOP"
    status = 422
    title = "Unknown shop"


def add_test_routes(app: FastAPI) -> None:
    @app.post("/_test/echo")
    async def echo(payload: Payload) -> Payload:
        return payload

    @app.get("/_test/boom")
    async def boom() -> None:
        raise Exception("secret internal detail")

    @app.get("/_test/domain-error")
    async def domain_error() -> None:
        raise _Teapot("shop-x is not in the catalog", shop_id="shop-x")

    @app.get("/_test/context")
    async def context() -> dict[str, object]:
        return dict(structlog.contextvars.get_contextvars())


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    app = create_app(settings)
    add_test_routes(app)
    return app


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async for c in client_for(app):
        yield c
