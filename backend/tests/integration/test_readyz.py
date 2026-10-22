import pytest

from spm.api.app import create_app
from tests.conftest import client_for, make_settings

pytestmark = pytest.mark.integration


async def test_readyz_ok_when_db_and_redis_up() -> None:
    app = create_app(
        make_settings(
            database_url="postgresql+asyncpg://spm:spm@localhost:5433/spm_test",
            redis_url="redis://localhost:6380/15",
        )
    )
    async for client in client_for(app):
        response = await client.get("/readyz")
    assert response.status_code == 200, response.text
    assert response.json() == {"status": "ok", "checks": {"db": "ok", "redis": "ok"}}
