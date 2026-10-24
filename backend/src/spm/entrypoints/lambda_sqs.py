"""AWS Lambda handler for SQS queues fed by EventBridge rules (one queue per consumer group).

Each record body is an EventBridge event: ``detail-type`` is the event type and ``detail`` the
envelope (§3.1). Records are dispatched to the registered consumer; failures are reported as
``batchItemFailures`` so SQS retries only those (and eventually moves them to the DLQ).
"""

import asyncio
import json
from collections.abc import Mapping, Sequence
from typing import Any

import structlog

from spm.core.events import Event
from spm.ports import Handler

log = structlog.get_logger("spm.lambda.sqs")

# One loop for the life of the Lambda container: pooled DB/Redis connections are bound to it.
_LOOP: asyncio.AbstractEventLoop | None = None
_HANDLERS: dict[str, Handler] | None = None


def parse_record(record: Mapping[str, Any]) -> tuple[str, Event]:
    body = json.loads(record["body"])
    event = Event.from_dict(body["detail"])
    detail_type = str(body.get("detail-type") or event.type)
    return detail_type, event


async def process_records(
    records: Sequence[Mapping[str, Any]], handlers: Mapping[str, Handler]
) -> list[str]:
    """Run each record through its handler; return the message ids that failed."""
    failed: list[str] = []
    for record in records:
        message_id = str(record.get("messageId", ""))
        try:
            detail_type, event = parse_record(record)
            handler = handlers.get(detail_type)
            if handler is None:
                log.info("sqs_record_skipped", message_id=message_id, detail_type=detail_type)
                continue
            await handler(event)
        except Exception:
            log.exception("sqs_record_failed", message_id=message_id)
            failed.append(message_id)
    return failed


async def _build_handlers() -> dict[str, Handler]:
    from spm.config import get_settings
    from spm.container import build_container
    from spm.logging import configure_logging
    from spm.workers.scoring import consumer_specs

    settings = get_settings()
    configure_logging(settings)
    container = await build_container(settings)
    return {t: spec.handler for spec in consumer_specs(container) for t in spec.types}


def handler(event: Mapping[str, Any], context: Any) -> dict[str, list[dict[str, str]]]:
    global _LOOP, _HANDLERS
    if _LOOP is None:
        _LOOP = asyncio.new_event_loop()
    if _HANDLERS is None:
        _HANDLERS = _LOOP.run_until_complete(_build_handlers())
    failed = _LOOP.run_until_complete(process_records(list(event.get("Records", [])), _HANDLERS))
    return {"batchItemFailures": [{"itemIdentifier": message_id} for message_id in failed]}
