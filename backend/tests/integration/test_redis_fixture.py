import pytest
import redis.asyncio as aioredis

pytestmark = pytest.mark.integration


async def test_redis_fixture_uses_clean_db_15(redis_client: aioredis.Redis) -> None:
    assert redis_client.connection_pool.connection_kwargs["db"] == 15
    assert await redis_client.dbsize() == 0
    await redis_client.set("spm:test", "1")
    assert await redis_client.get("spm:test") == b"1"
