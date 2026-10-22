"""Redis Streams bus: ``spm:events`` with one consumer group per consumer (MODULES.md §3.1-3.2).

Delivery is at least once: an entry is ACKed only after its handler succeeds. Entries left
pending (handler failed, or the process died) are reclaimed with XAUTOCLAIM and retried; after
``max_deliveries`` they move to ``spm:events:dlq``.
"""

import asyncio
import contextlib
import os
import socket
import time
from typing import Any, cast

import structlog
from redis.asyncio import Redis
from redis.exceptions import ResponseError

from spm.adapters.redis.keys import EVENTS_DLQ_STREAM, EVENTS_STREAM
from spm.constants import (
    EVENT_MAX_DELIVERIES,
    STREAM_BLOCK_MS,
    STREAM_CLAIM_INTERVAL_S,
    STREAM_CLAIM_MIN_IDLE_MS,
    STREAM_MAXLEN,
    STREAM_READ_COUNT,
)
from spm.core.events import Event
from spm.ports import Handler

log = structlog.get_logger("spm.bus.redis")


def _text(value: bytes | str) -> str:
    return value.decode() if isinstance(value, bytes) else value


class RedisStreamsBus:
    def __init__(
        self,
        redis: "Redis",
        *,
        stream: str = EVENTS_STREAM,
        dlq: str = EVENTS_DLQ_STREAM,
        consumer_name: str | None = None,
        block_ms: int = STREAM_BLOCK_MS,
        claim_interval_s: float = STREAM_CLAIM_INTERVAL_S,
        claim_min_idle_ms: int = STREAM_CLAIM_MIN_IDLE_MS,
        max_deliveries: int = EVENT_MAX_DELIVERIES,
    ) -> None:
        self._redis = redis
        self.stream = stream
        self.dlq = dlq
        self.consumer_name = consumer_name or f"{socket.gethostname()}-{os.getpid()}"
        self._block_ms = block_ms
        self._claim_interval_s = claim_interval_s
        self._claim_min_idle_ms = claim_min_idle_ms
        self._max_deliveries = max_deliveries
        self._last_errors: dict[str, str] = {}

    # ----------------------------------------------------------------------- publishing
    async def publish(self, events: list[Event]) -> None:
        if not events:
            return
        async with self._redis.pipeline(transaction=False) as pipe:
            for event in events:
                pipe.xadd(
                    self.stream,
                    {"type": event.type, "body": event.to_json()},
                    maxlen=STREAM_MAXLEN,
                    approximate=True,
                )
            await pipe.execute()

    # ------------------------------------------------------------------------ consuming
    async def ensure_group(self, group: str) -> None:
        try:
            await self._redis.xgroup_create(self.stream, group, id="$", mkstream=True)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def run(
        self, group: str, types: set[str], handler: Handler, *, stop: asyncio.Event
    ) -> None:
        await self.ensure_group(group)
        log.info("consumer_started", group=group, consumer=self.consumer_name, types=sorted(types))
        next_claim = time.monotonic()  # reclaim leftovers from a previous run right away
        while not stop.is_set():
            if time.monotonic() >= next_claim:
                await self.reclaim(group, types, handler)
                next_claim = time.monotonic() + self._claim_interval_s
            entries = await self._read(group, stop)
            for entry_id, fields in entries:
                await self._deliver(group, types, handler, entry_id, fields, deliveries=1)

    async def _read(self, group: str, stop: asyncio.Event) -> list[tuple[str, dict[str, Any]]]:
        """XREADGROUP … BLOCK, abandoned as soon as ``stop`` is set."""
        read = asyncio.ensure_future(
            self._redis.xreadgroup(
                group,
                self.consumer_name,
                {self.stream: ">"},
                count=STREAM_READ_COUNT,
                block=self._block_ms,
            )
        )
        stopper = asyncio.ensure_future(stop.wait())
        await asyncio.wait({read, stopper}, return_when=asyncio.FIRST_COMPLETED)
        stopper.cancel()
        if not read.done():
            read.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await read
            return []
        response = cast(list[Any], read.result() or [])
        return [
            (_text(entry_id), fields)
            for _stream, stream_entries in response
            for entry_id, fields in stream_entries
        ]

    async def _deliver(
        self,
        group: str,
        types: set[str],
        handler: Handler,
        entry_id: str,
        fields: dict[Any, Any],
        *,
        deliveries: int,
    ) -> None:
        values = {_text(k): v for k, v in fields.items()}
        event_type = _text(values.get("type", b""))
        if event_type not in types:
            await self._redis.xack(self.stream, group, entry_id)
            return
        try:
            await handler(Event.from_json(values["body"]))
        except Exception as exc:
            self._last_errors[entry_id] = repr(exc)
            log.warning(
                "event_handler_failed", group=group, entry_id=entry_id, deliveries=deliveries,
                error=repr(exc),
            )  # fmt: skip
            if deliveries >= self._max_deliveries:
                await self._dead_letter(group, entry_id, values, deliveries)
            return
        self._last_errors.pop(entry_id, None)
        await self._redis.xack(self.stream, group, entry_id)

    async def reclaim(self, group: str, types: set[str], handler: Handler) -> int:
        """XAUTOCLAIM entries idle longer than the threshold and retry them. Returns how many."""
        claimed_total = 0
        start = "0-0"
        while True:
            next_start, claimed, _deleted = await self._redis.xautoclaim(
                self.stream, group, self.consumer_name, self._claim_min_idle_ms, start, count=100
            )
            for entry_id_raw, fields in claimed:
                entry_id = _text(entry_id_raw)
                if fields is None:  # trimmed from the stream while pending
                    await self._redis.xack(self.stream, group, entry_id)
                    continue
                pending = await self._redis.xpending_range(
                    self.stream, group, min=entry_id, max=entry_id, count=1
                )
                deliveries = int(pending[0]["times_delivered"]) if pending else self._max_deliveries
                claimed_total += 1
                values = {_text(k): v for k, v in fields.items()}
                if deliveries > self._max_deliveries:
                    # Earlier deliveries never reported back (e.g. the worker crashed mid-handler).
                    await self._dead_letter(group, entry_id, values, deliveries)
                else:
                    await self._deliver(
                        group, types, handler, entry_id, fields, deliveries=deliveries
                    )
            start = _text(next_start)
            if start == "0-0":
                return claimed_total

    async def _dead_letter(
        self, group: str, entry_id: str, values: dict[str, Any], deliveries: int
    ) -> None:
        error = self._last_errors.pop(entry_id, "max deliveries exceeded")
        await self._redis.xadd(
            self.dlq,
            {
                "type": values.get("type", b""),
                "body": values.get("body", b""),
                "group": group,
                "entry_id": entry_id,
                "deliveries": deliveries,
                "error": error,
            },
            maxlen=STREAM_MAXLEN,
            approximate=True,
        )
        await self._redis.xack(self.stream, group, entry_id)
        log.error("event_dead_lettered", group=group, entry_id=entry_id, deliveries=deliveries)
