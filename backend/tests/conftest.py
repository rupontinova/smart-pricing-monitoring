from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI

from spm.config import Settings


def make_settings(**overrides: object) -> Settings:
    """Settings isolated from the developer's environment and `.env` file."""
    values: dict[str, object] = {
        "env": "test",
        "database_url": "",
        "redis_url": "",
        "jwt_secret": "test-jwt-secret-of-at-least-32-bytes",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


@pytest.fixture
def settings() -> Settings:
    return make_settings()


async def client_for(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """An httpx client bound to the app, with the app's lifespan running."""
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
