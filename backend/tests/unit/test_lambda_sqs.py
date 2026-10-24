import json
from typing import Any

from spm.core.events import Event
from spm.entrypoints.lambda_sqs import process_records
from tests.unit.events_helpers import price_reported


def _record(message_id: str, body: Any) -> dict[str, Any]:
    return {"messageId": message_id, "body": body if isinstance(body, str) else json.dumps(body)}


def _eventbridge(event: Event) -> dict[str, Any]:
    return {"source": "spm", "detail-type": event.type, "detail": json.loads(event.to_json())}


async def test_malformed_record_is_a_batch_failure_others_succeed() -> None:
    handled: list[Event] = []

    async def handler(event: Event) -> None:
        handled.append(event)

    good = price_reported(0)
    records = [
        _record("ok-1", _eventbridge(good)),
        _record("bad-json", "{not json"),
        _record("bad-envelope", {"detail-type": "price.reported", "detail": {"id": "x"}}),
        _record("other-type", {**_eventbridge(good), "detail-type": "band.updated"}),
    ]
    failed = await process_records(records, {"price.reported": handler})
    assert failed == ["bad-json", "bad-envelope"]
    assert handled == [good]  # band.updated has no consumer here: skipped (acknowledged)


async def test_handler_exception_is_a_batch_failure() -> None:
    async def boom(event: Event) -> None:
        raise ConnectionError("db down")

    failed = await process_records(
        [_record("m1", _eventbridge(price_reported()))], {"price.reported": boom}
    )
    assert failed == ["m1"]
