"""Dependency wiring: builds adapters from ``Settings``.

Real so far: ``settings``, ``clock``, ``ids``; with ``SPM_DATABASE_URL`` the engine, session factory
and catalog cache (M03/M04); with ``SPM_REDIS_URL`` the Redis client and shop geo index (M04).
The other slots are filled by the modules that introduce them (M07 bus, M16 notifier, M18 metrics).
"""

from dataclasses import dataclass
from typing import Any

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from spm.adapters.ids import UuidV7Generator
from spm.adapters.postgres.catalog_repo import PgCatalogReader
from spm.adapters.postgres.db import make_engine, make_session_factory
from spm.adapters.redis.hot_cache import RedisHotCache
from spm.config import Settings
from spm.core.clock import SystemClock
from spm.ports import Clock, IdGenerator, ShopGeoIndex
from spm.services.catalog_cache import CatalogCache

REDIS_CONNECT_TIMEOUT_S = 2.0
REDIS_SOCKET_TIMEOUT_S = 5.0


@dataclass(slots=True)
class Container:
    settings: Settings
    clock: Clock
    ids: IdGenerator
    db_engine: AsyncEngine | None = None
    session_factory: async_sessionmaker[AsyncSession] | None = None
    redis: "Redis | None" = None
    catalog: CatalogCache | None = None
    shop_geo: ShopGeoIndex | None = None
    bus: Any = None
    notifier: Any = None
    metrics: Any = None

    async def close(self) -> None:
        """Release connections held by adapters."""
        if self.redis is not None:
            await self.redis.aclose()
        if self.db_engine is not None:
            await self.db_engine.dispose()


def make_redis(url: str) -> "Redis":
    return Redis.from_url(
        url,
        decode_responses=True,
        socket_connect_timeout=REDIS_CONNECT_TIMEOUT_S,
        socket_timeout=REDIS_SOCKET_TIMEOUT_S,
    )


async def build_container(settings: Settings) -> Container:
    """Build adapters for the configured services. Lazy: nothing connects until first use."""
    container = Container(settings=settings, clock=SystemClock(), ids=UuidV7Generator())
    if settings.database_url:
        container.db_engine = make_engine(settings)
        container.session_factory = make_session_factory(container.db_engine)
        container.catalog = CatalogCache(
            PgCatalogReader(container.session_factory), container.clock
        )
    if settings.redis_url:
        container.redis = make_redis(settings.redis_url)
        container.shop_geo = RedisHotCache(container.redis)
    return container
