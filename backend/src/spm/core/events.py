"""The event envelope (MODULES.md §3.1) and one versioned payload model per event type.

Unlike the rest of ``spm.core`` this module uses pydantic (payload validation) and orjson (wire
format), as M07 specifies; both are pure, with no I/O.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, ClassVar, Literal, Protocol, Self
from uuid import UUID

import orjson
from pydantic import BaseModel, ConfigDict

from spm.core.enums import AnomalyKind, Confidence, Direction, Verdict

# ------------------------------------------------------------------------------- payloads


class Payload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    TYPE: ClassVar[str]
    VERSION: ClassVar[int] = 1


class PriceReported(Payload):
    TYPE = "price.reported"
    report_id: UUID
    received_at: datetime
    product_id: str
    shop_id: str
    area_id: str
    reporter_id: UUID
    norm_price: Decimal  # serialized as a string: exact
    observed_at: datetime


class AnomalyDetected(Payload):
    TYPE = "anomaly.detected"
    anomaly_id: UUID
    kind: AnomalyKind
    direction: Direction
    product_id: str
    area_id: str
    shop_id: str | None
    observed_price: float
    reference_price: float
    magnitude_pct: float
    report_ids: list[UUID]
    detected_at: datetime


class BandUpdated(Payload):
    TYPE = "band.updated"
    product_id: str
    area_id: str
    p25: float
    p50: float
    p75: float
    confidence: Confidence
    computed_at: datetime


class PriceChecked(Payload):
    TYPE = "price.checked"
    check_id: UUID
    product_id: str
    area_id: str
    verdict: Verdict
    quoted_norm_price: Decimal
    cheapest_norm_price: Decimal | None
    base_quantity: Decimal
    potential_saving: Decimal | None


class DailyTick(Payload):
    TYPE = "daily.tick"
    job: Literal["rollup", "digest", "maintenance", "outbox_sweep", "deferred_alerts"]
    area_id: str | None
    for_date: date


PAYLOADS: dict[str, type[Payload]] = {
    p.TYPE: p for p in (PriceReported, AnomalyDetected, BandUpdated, PriceChecked, DailyTick)
}
EVENT_TYPES: frozenset[str] = frozenset(PAYLOADS)


class UnknownEventType(ValueError):
    pass


def payload_model(event_type: str) -> type[Payload]:
    try:
        return PAYLOADS[event_type]
    except KeyError:
        raise UnknownEventType(f"unknown event type {event_type!r}") from None


# ------------------------------------------------------------------------------- envelope


@dataclass(frozen=True, slots=True)
class Event:
    id: UUID  # UUIDv7, unique per event: the idempotency key
    type: str  # e.g. "price.reported"
    version: int
    occurred_at: datetime
    source: str  # spm.api | spm.worker.scoring | spm.scheduler | …
    correlation_id: str | None
    data: dict[str, Any] = field(default_factory=dict)  # JSON-ready (see make_event)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "type": self.type,
            "version": self.version,
            "occurred_at": self.occurred_at,
            "source": self.source,
            "correlation_id": self.correlation_id,
            "data": self.data,
        }

    def to_json(self) -> bytes:
        return orjson.dumps(self.to_dict(), option=orjson.OPT_UTC_Z)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Self:
        occurred = raw["occurred_at"]
        return cls(
            id=UUID(str(raw["id"])),
            type=str(raw["type"]),
            version=int(raw["version"]),
            occurred_at=occurred
            if isinstance(occurred, datetime)
            else datetime.fromisoformat(occurred),
            source=str(raw["source"]),
            correlation_id=raw.get("correlation_id"),
            data=dict(raw.get("data") or {}),
        )

    @classmethod
    def from_json(cls, raw: bytes | str) -> Self:
        return cls.from_dict(orjson.loads(raw))

    def payload(self) -> Payload:
        """The typed, validated payload."""
        return payload_model(self.type).model_validate(self.data)


class _Clock(Protocol):
    def now(self) -> datetime: ...


class _Ids(Protocol):
    def new(self) -> UUID: ...


def make_event(
    type: str,
    data: Payload | dict[str, Any],
    source: str,
    correlation_id: str | None,
    clock: _Clock,
    ids: _Ids,
) -> Event:
    """Build an envelope; ``data`` is validated against the type's payload model."""
    model = payload_model(type)
    payload = data if isinstance(data, Payload) else model.model_validate(data)
    if not isinstance(payload, model):
        raise TypeError(
            f"{type!r} needs a {model.__name__} payload, got {payload.__class__.__name__}"
        )
    return Event(
        id=ids.new(),
        type=type,
        version=model.VERSION,
        occurred_at=clock.now(),
        source=source,
        correlation_id=correlation_id,
        data=payload.model_dump(mode="json"),
    )
