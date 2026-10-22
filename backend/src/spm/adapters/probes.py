"""Connectivity probes used by ``/readyz``."""

import asyncio

import redis.asyncio as aioredis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

PROBE_TIMEOUT_S = 2.0


async def ping_engine(engine: AsyncEngine, timeout_s: float = PROBE_TIMEOUT_S) -> None:
    """Raise if a pooled connection from ``engine`` can't answer ``SELECT 1`` in time."""
    async with asyncio.timeout(timeout_s), engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


async def ping_redis(url: str, timeout_s: float = PROBE_TIMEOUT_S) -> None:
    """Raise if Redis at ``url`` doesn't answer ``PING`` within ``timeout_s``."""
    client = aioredis.from_url(url, socket_connect_timeout=timeout_s, socket_timeout=timeout_s)
    try:
        async with asyncio.timeout(timeout_s + 1):
            await client.ping()
    finally:
        await client.aclose()
