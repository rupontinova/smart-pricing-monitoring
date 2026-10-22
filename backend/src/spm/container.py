"""Dependency wiring: builds adapters from ``Settings``.

Real so far: ``settings``, ``clock``, ``ids`` and, when ``SPM_DATABASE_URL`` is set, the database
engine and session factory (M03). The other slots are filled by the modules that introduce them
(M07 bus, M16 notifier, M18 metrics).
"""

from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from spm.adapters.ids import UuidV7Generator
from spm.adapters.postgres.db import make_engine, make_session_factory
from spm.config import Settings
from spm.core.clock import SystemClock
from spm.ports import Clock, IdGenerator


@dataclass(slots=True)
class Container:
    settings: Settings
    clock: Clock
    ids: IdGenerator
    db_engine: AsyncEngine | None = None
    session_factory: async_sessionmaker[AsyncSession] | None = None
    redis: Any = None
    bus: Any = None
    notifier: Any = None
    metrics: Any = None

    async def close(self) -> None:
        """Release connections held by adapters."""
        if self.db_engine is not None:
            await self.db_engine.dispose()


async def build_container(settings: Settings) -> Container:
    container = Container(settings=settings, clock=SystemClock(), ids=UuidV7Generator())
    if settings.database_url:
        # Lazy: no connection is opened until first use.
        container.db_engine = make_engine(settings)
        container.session_factory = make_session_factory(container.db_engine)
    return container
