"""Connectivity probes used by ``/readyz``."""

import asyncio

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

PROBE_TIMEOUT_S = 2.0


async def ping_engine(engine: AsyncEngine, timeout_s: float = PROBE_TIMEOUT_S) -> None:
    """Raise if a pooled connection from ``engine`` can't answer ``SELECT 1`` in time."""
    async with asyncio.timeout(timeout_s), engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


async def ping_redis(client: "Redis", timeout_s: float = PROBE_TIMEOUT_S) -> None:
    """Raise if Redis doesn't answer ``PING`` in time."""
    async with asyncio.timeout(timeout_s):
        await client.ping()
