"""Dependency wiring: builds adapters from ``Settings``.

Real so far: ``settings``, ``clock``, ``ids``; with ``SPM_DATABASE_URL`` the engine, session factory
and catalog cache (M03/M04); with ``SPM_REDIS_URL`` the Redis client, shop geo index (M04) and
rate limiter (M06); the event bus from ``SPM_EVENT_BUS`` (M07); tokens, metrics and the unit of
work factory (M08).
The other slots are filled by the modules that introduce them (M07 bus, M16 notifier, M18 metrics).
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import structlog
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from spm.adapters.ids import UuidV7Generator
from spm.adapters.log_metrics import LogMetrics
from spm.adapters.memory.events import MemoryBus
from spm.adapters.postgres.catalog_repo import PgCatalogReader
from spm.adapters.postgres.db import make_engine, make_session_factory
from spm.adapters.postgres.uow import PgUnitOfWork
from spm.adapters.redis.hot_cache import RedisHotCache
from spm.adapters.redis.rate_limiter import RedisRateLimiter
from spm.adapters.redis.streams_bus import RedisStreamsBus
from spm.adapters.tokens import JwtTokens
from spm.config import Settings
from spm.constants import STREAM_BLOCK_MS
from spm.core.clock import SystemClock
from spm.ports import (
    Clock,
    EventConsumer,
    EventPublisher,
    HotCache,
    IdGenerator,
    Metrics,
    RateLimiter,
    ShopGeoIndex,
)
from spm.services.catalog_cache import CatalogCache

log = structlog.get_logger("spm.container")

REDIS_CONNECT_TIMEOUT_S = 2.0
REDIS_SOCKET_TIMEOUT_S = 5.0


@dataclass(slots=True)
class Container:
    settings: Settings
    clock: Clock
    ids: IdGenerator
    db_engine: AsyncEngine | None = None
    session_factory: async_sessionmaker[AsyncSession] | None = None
    # Opens a unit of work (one transaction). PgUnitOfWork, or MemoryUnitOfWork in tests.
    uow_factory: Callable[[], Any] | None = None
    tokens: JwtTokens | None = None
    redis: "Redis | None" = None
    stream_redis: "Redis | None" = None  # the event bus's own client (long blocking reads)
    catalog: CatalogCache | None = None
    shop_geo: ShopGeoIndex | None = None
    hot_cache: HotCache | None = None
    rate_limiter: RateLimiter | None = None
    bus: EventPublisher | None = None
    consumer: EventConsumer | None = None  # None when the bus can't be consumed (EventBridge)
    notifier: Any = None
    metrics: Metrics | None = None

    async def close(self) -> None:
        """Release connections held by adapters."""
        for client in (self.redis, self.stream_redis):
            if client is not None:
                await client.aclose()
        if self.db_engine is not None:
            await self.db_engine.dispose()


def make_redis(url: str, *, socket_timeout_s: float = REDIS_SOCKET_TIMEOUT_S) -> "Redis":
    return Redis.from_url(
        url,
        decode_responses=True,
        socket_connect_timeout=REDIS_CONNECT_TIMEOUT_S,
        socket_timeout=socket_timeout_s,
    )


async def build_container(settings: Settings) -> Container:
    """Build adapters for the configured services. Lazy: nothing connects until first use."""
    clock = SystemClock()
    container = Container(
        settings=settings,
        clock=clock,
        ids=UuidV7Generator(),
        tokens=JwtTokens(settings.jwt_secret.get_secret_value(), settings.jwt_ttl_d, clock),
        metrics=LogMetrics(),  # EMF for AWS arrives in M18
    )
    if settings.database_url:
        container.db_engine = make_engine(settings)
        session_factory = make_session_factory(container.db_engine)
        container.session_factory = session_factory
        container.uow_factory = lambda: PgUnitOfWork(session_factory)
        container.catalog = CatalogCache(
            PgCatalogReader(container.session_factory), container.clock
        )
    if settings.redis_url:
        container.redis = make_redis(settings.redis_url)
        hot_cache = RedisHotCache(container.redis)
        container.shop_geo = hot_cache
        container.hot_cache = hot_cache
        container.rate_limiter = RedisRateLimiter(container.redis, container.clock)
    _build_bus(container)
    return container


def _build_bus(container: Container) -> None:
    """``SPM_EVENT_BUS``: memory (tests, simulator) | redis (local) | eventbridge (AWS)."""
    kind = container.settings.event_bus
    if kind == "memory":
        bus = MemoryBus()
        container.bus, container.consumer = bus, bus
    elif kind == "redis":
        if container.redis is None:
            log.warning("event_bus_unavailable", reason="SPM_EVENT_BUS=redis but no SPM_REDIS_URL")
            return
        # Its own client: XREADGROUP blocks for STREAM_BLOCK_MS, which must stay well below the
        # socket timeout (with the shared 5 s timeout, idle reads timed out and killed the worker).
        container.stream_redis = make_redis(
            container.settings.redis_url, socket_timeout_s=STREAM_BLOCK_MS / 1000 + 10
        )
        streams = RedisStreamsBus(container.stream_redis)
        container.bus, container.consumer = streams, streams
    else:
        from spm.adapters.aws.eventbridge_bus import make_eventbridge_publisher

        container.bus = make_eventbridge_publisher(container.settings.aws_event_bus_name)
