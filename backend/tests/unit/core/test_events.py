import uuid
from datetime import date
from decimal import Decimal

import orjson
import pytest
from pydantic import ValidationError

from spm.adapters.memory import FixedClock, SequentialIds
from spm.core.events import (
    EVENT_TYPES,
    BandUpdated,
    DailyTick,
    Event,
    PriceReported,
    UnknownEventType,
    make_event,
)
from tests.unit.events_helpers import T0, price_reported


def test_every_contract_event_type_has_a_payload_model() -> None:
    # MODULES.md §3.1
    assert {
        "price.reported", "anomaly.detected", "band.updated", "price.checked", "daily.tick",
    } == EVENT_TYPES  # fmt: skip


def test_make_event_builds_envelope() -> None:
    event = price_reported()
    assert event.type == "price.reported"
    assert event.version == 1
    assert event.occurred_at == T0
    assert event.source == "spm.api"
    assert event.correlation_id == "req-0"
    assert event.id.version == 7
    assert event.data["norm_price"] == "80.0000"  # Decimal kept exact as a string
    assert event.data["report_id"] == str(uuid.UUID(int=1))


def test_json_round_trip_matches_contract_shape() -> None:
    event = price_reported()
    wire = orjson.loads(event.to_json())
    assert set(wire) == {"id", "type", "version", "occurred_at", "source", "correlation_id", "data"}
    assert wire["occurred_at"] == "2026-10-10T08:15:02.123000Z"
    assert Event.from_json(event.to_json()) == event


def test_payload_is_typed() -> None:
    payload = price_reported().payload()
    assert isinstance(payload, PriceReported)
    assert payload.norm_price == Decimal("80.0000")


def test_make_event_accepts_dict_and_validates() -> None:
    clock, ids = FixedClock(T0), SequentialIds()
    event = make_event(
        "daily.tick", {"job": "rollup", "area_id": None, "for_date": "2026-10-10"}, "spm.scheduler",
        None, clock, ids,
    )  # fmt: skip
    payload = event.payload()
    assert isinstance(payload, DailyTick) and payload.for_date == date(2026, 10, 10)
    with pytest.raises(ValidationError):
        make_event("daily.tick", {"job": "nap", "area_id": None, "for_date": "2026-10-10"},
                   "spm.scheduler", None, clock, ids)  # fmt: skip
    with pytest.raises(ValidationError):
        make_event("price.reported", {"report_id": "x"}, "spm.api", None, clock, ids)


def test_make_event_rejects_unknown_type_and_mismatched_payload() -> None:
    clock, ids = FixedClock(T0), SequentialIds()
    with pytest.raises(UnknownEventType):
        make_event("price.teleported", {}, "spm.api", None, clock, ids)
    band = BandUpdated(product_id="onion", area_id="a", p25=1, p50=2, p75=3,
                       confidence="high", computed_at=T0)  # fmt: skip
    with pytest.raises(TypeError, match="PriceReported"):
        make_event("price.reported", band, "spm.api", None, clock, ids)
