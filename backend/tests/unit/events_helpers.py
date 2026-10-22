"""Shared builders for event tests."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from spm.adapters.memory import FixedClock, SequentialIds
from spm.core.events import Event, PriceReported, make_event

T0 = datetime(2026, 10, 10, 8, 15, 2, 123000, tzinfo=UTC)


def price_reported(
    n: int = 0, *, clock: FixedClock | None = None, ids: SequentialIds | None = None
) -> Event:
    clock = clock or FixedClock(T0)
    payload = PriceReported(
        report_id=uuid.UUID(int=n + 1),
        received_at=clock.now(),
        product_id="onion",
        shop_id="shop-a",
        area_id="demo-central",
        reporter_id=uuid.UUID(int=999),
        norm_price=Decimal("80.0000") + n,
        observed_at=clock.now(),
    )
    return make_event(
        "price.reported", payload, "spm.api", f"req-{n}", clock, ids or SequentialIds()
    )
