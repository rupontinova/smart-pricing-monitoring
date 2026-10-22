import json
from typing import Any

import pytest

from spm.adapters.aws.eventbridge_bus import EventBridgePublisher, PublishError
from spm.adapters.memory import SequentialIds
from tests.unit.events_helpers import price_reported


class StubEventBridge:
    def __init__(self, fail_index: int | None = None) -> None:
        self.calls: list[list[dict[str, Any]]] = []
        self.fail_index = fail_index

    def put_events(self, *, Entries: list[dict[str, Any]]) -> dict[str, Any]:
        self.calls.append(Entries)
        results: list[dict[str, Any]] = [{"EventId": str(i)} for i in range(len(Entries))]
        if self.fail_index is not None:
            results[self.fail_index] = {"ErrorCode": "ThrottlingException", "ErrorMessage": "slow"}
        return {"FailedEntryCount": int(self.fail_index is not None), "Entries": results}


async def test_23_events_published_in_3_batches() -> None:
    client = StubEventBridge()
    ids = SequentialIds()
    events = [price_reported(i, ids=ids) for i in range(23)]
    await EventBridgePublisher(client, "spm-bus").publish(events)
    assert [len(c) for c in client.calls] == [10, 10, 3]
    first = client.calls[0][0]
    assert first["Source"] == "spm"
    assert first["DetailType"] == "price.reported"
    assert first["EventBusName"] == "spm-bus"
    assert json.loads(first["Detail"])["id"] == str(events[0].id)


async def test_failed_entries_raise_so_outbox_retries() -> None:
    client = StubEventBridge(fail_index=1)
    with pytest.raises(PublishError, match="ThrottlingException"):
        await EventBridgePublisher(client, "spm-bus").publish(
            [price_reported(0), price_reported(1)]
        )
