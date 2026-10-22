"""EventBridge publisher. Consumption on AWS is SQS -> Lambda (M13), not this class."""

import asyncio
from typing import Any

import structlog

from spm.core.events import Event

log = structlog.get_logger("spm.bus.eventbridge")

PUT_EVENTS_MAX_ENTRIES = 10  # EventBridge limit per PutEvents call
EVENT_SOURCE = "spm"


class PublishError(RuntimeError):
    pass


class EventBridgePublisher:
    """``EventPublisher`` over ``PutEvents`` (boto3 is synchronous: calls run in a thread)."""

    def __init__(self, client: Any, event_bus_name: str) -> None:
        self._client = client
        self._bus = event_bus_name

    async def publish(self, events: list[Event]) -> None:
        for start in range(0, len(events), PUT_EVENTS_MAX_ENTRIES):
            batch = events[start : start + PUT_EVENTS_MAX_ENTRIES]
            entries = [
                {
                    "Source": EVENT_SOURCE,
                    "DetailType": e.type,
                    "Detail": e.to_json().decode(),
                    "EventBusName": self._bus,
                }
                for e in batch
            ]
            response = await asyncio.to_thread(self._client.put_events, Entries=entries)
            if response.get("FailedEntryCount", 0):
                failed = [
                    (str(e.id), r.get("ErrorCode"))
                    for e, r in zip(batch, response.get("Entries", []), strict=False)
                    if r.get("ErrorCode")
                ]
                raise PublishError(f"EventBridge rejected {len(failed)} events: {failed}")


def make_eventbridge_publisher(event_bus_name: str) -> EventBridgePublisher:
    import boto3  # imported lazily: only the AWS deployment needs it

    return EventBridgePublisher(boto3.client("events"), event_bus_name)
