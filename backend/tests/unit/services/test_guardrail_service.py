import math
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from spm.adapters.memory import (
    FixedClock,
    MemoryCatalogReader,
    MemoryRateLimiter,
    MemoryReportRepo,
    MemoryUnitOfWork,
    StoredReport,
)
from spm.core.enums import Confidence
from spm.core.errors import (
    ObservedAtOutOfRange,
    PriceOutOfBounds,
    QuantityOutOfRange,
    RateLimited,
    TooFarFromShop,
    UnknownProduct,
    UnknownShop,
)
from spm.core.geo import EARTH_RADIUS_M
from spm.core.models import FairBand, GeoPoint, LocationFix, RawPriceInput
from spm.core.normalizer import normalize as real_normalize
from spm.services import guardrail_service
from spm.services.catalog_cache import CatalogCache
from spm.services.guardrail_service import GuardrailService

T0 = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
REPORTER = uuid.UUID(int=7)
SHOP_A = GeoPoint(23.783498, 90.4073)
SEED_PRODUCT_IDS = {p.id for p in MemoryCatalogReader.from_seeds().catalog.products}


class Harness:
    def __init__(self, band_p50: float | None = None) -> None:
        self.clock = FixedClock(T0)
        self.catalog = CatalogCache(MemoryCatalogReader.from_seeds(), self.clock)
        self.limiter = MemoryRateLimiter(self.clock)
        self.reports = MemoryReportRepo()
        self.band_p50 = band_p50
        self.service = GuardrailService(
            self.catalog,
            self.limiter,
            lambda: MemoryUnitOfWork(self.reports),
            self.clock,
            hot_cache=self if band_p50 is not None else None,
        )

    async def get_band(self, area_id: str, product_id: str) -> FairBand | None:
        assert self.band_p50 is not None
        p = self.band_p50
        return FairBand(product_id, area_id, p, p, p, 1.0, 10.0, 5.0, 3, Confidence.HIGH,
                        T0, 2.0, 48, None, T0)  # fmt: skip

    async def admit(
        self,
        product: str = "onion",
        price: str = "60",
        quantity: str = "1",
        unit: str = "kg",
        *,
        shop_id: str = "shop-a",
        observed_at: datetime | None = None,
        fix: LocationFix | None = None,
        **kw: Any,
    ) -> guardrail_service.Admission:
        is_id = product in SEED_PRODUCT_IDS
        raw = RawPriceInput(
            product if is_id else None,
            None if is_id else product,
            Decimal(price),
            Decimal(quantity),
            unit,
        )
        return await self.service.admit(
            REPORTER, raw, shop_id, observed_at or self.clock.now(), fix, **kw
        )


def _fix(meters_north_of_shop_a: float) -> LocationFix:
    lat = SHOP_A.lat + math.degrees(meters_north_of_shop_a / EARTH_RADIUS_M)
    return LocationFix(GeoPoint(lat, SHOP_A.lng), 10.0)


async def test_admit_happy_path() -> None:
    h = Harness()
    admission = await h.admit("Piyaz", "30", "500", "g", fix=_fix(120))
    assert admission.normalized.product_id == "onion"
    assert admission.normalized.norm_price == Decimal("60.0000")
    assert admission.shop.id == "shop-a"
    assert admission.location_verified
    assert admission.distance_m == pytest.approx(120, abs=1)
    assert admission.duplicate_of is None


async def test_admit_without_location_is_unverified() -> None:
    admission = await Harness().admit()
    assert (admission.location_verified, admission.distance_m) == (False, None)


async def test_rate_limited_request_never_reaches_normalizer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[RawPriceInput] = []
    real = real_normalize

    def spy(raw: RawPriceInput, catalog: Any) -> Any:
        calls.append(raw)
        return real(raw, catalog)

    monkeypatch.setattr("spm.services.guardrail_service.normalize", spy)
    h = Harness()
    for _ in range(30):
        await h.admit(price="60")
    assert len(calls) == 30
    with pytest.raises(RateLimited) as info:
        await h.admit()
    assert len(calls) == 30  # the 31st stopped before normalize
    assert info.value.extra["window"] == "hour"
    assert info.value.retry_after_s == 3600  # T0 is on the hour


async def test_daily_limit_applies_across_hours() -> None:
    h = Harness()
    accepted = 0
    with pytest.raises(RateLimited) as info:
        for _ in range(5):
            for _ in range(30):
                await h.admit()
                accepted += 1
            h.clock.advance(hours=1)
    assert accepted == 100
    assert info.value.extra["window"] == "day"


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        # unknown product AND unknown shop -> the product error comes first (step 2 < 4)
        ({"product": "xyz", "shop_id": "nope"}, UnknownProduct),
        # stale AND unknown shop -> freshness first (3 < 4)
        ({"observed_at": T0 - timedelta(days=9), "shop_id": "nope"}, ObservedAtOutOfRange),
        # unknown shop AND out of bounds -> shop first (4 < 5)
        ({"shop_id": "nope", "price": "1"}, UnknownShop),
        # out of bounds AND too far -> bounds first (5 < 6)
        ({"price": "1", "fix": None}, PriceOutOfBounds),
    ],
)
async def test_checks_run_in_documented_order(
    kwargs: dict[str, Any], error: type[Exception]
) -> None:
    if "fix" in kwargs:
        kwargs["fix"] = _fix(5000)
    with pytest.raises(error):
        await Harness().admit(**kwargs)


async def test_too_far_stops_before_duplicate_lookup() -> None:
    h = Harness()
    with pytest.raises(TooFarFromShop):
        await h.admit(fix=_fix(5000))
    assert h.reports.duplicate_queries == 0


async def test_quantity_out_of_range_carries_hints() -> None:
    with pytest.raises(QuantityOutOfRange) as info:
        await Harness().admit("onion", "40", "500", "kg")
    assert info.value.extra["hints"][0]["message"] == "Did you mean 500 g instead of 500 kg?"  # type: ignore[index]


async def test_duplicate_detected_within_window() -> None:
    h = Harness()
    earlier = uuid.uuid4()
    h.reports.rows.append(
        StoredReport(
            earlier, REPORTER, "onion", "shop-a", Decimal("60.30"), T0 - timedelta(hours=2)
        )
    )
    assert (await h.admit(price="60")).duplicate_of == earlier
    assert (await h.admit(price="70")).duplicate_of is None


async def test_duplicate_ignored_outside_window_or_other_shop() -> None:
    h = Harness()
    h.reports.rows += [
        StoredReport(
            uuid.uuid4(), REPORTER, "onion", "shop-a", Decimal(60), T0 - timedelta(hours=7)
        ),
        StoredReport(uuid.uuid4(), REPORTER, "onion", "shop-b", Decimal(60), T0),
        StoredReport(uuid.uuid4(), uuid.uuid4(), "onion", "shop-a", Decimal(60), T0),
    ]
    assert (await h.admit(price="60")).duplicate_of is None


async def test_band_median_ranks_hints() -> None:
    # Onion at 600 for 1 kg is above the 500 maximum. Hints: "2 kg" (300/kg) and "12 kg" (50/kg).
    async def first_hint(band_p50: float) -> str:
        with pytest.raises(PriceOutOfBounds) as info:
            await Harness(band_p50=band_p50).admit("onion", "600", "1", "kg")
        hints = info.value.extra["hints"]
        assert isinstance(hints, list) and len(hints) == 2
        return str(hints[0]["message"])

    assert await first_hint(55.0) == "Did you mean 12 kg instead of 1 kg?"
    assert await first_hint(280.0) == "Did you mean 2 kg instead of 1 kg?"


async def test_backfill_allows_old_observations() -> None:
    admission = await Harness().admit(observed_at=T0 - timedelta(days=30), allow_backfill=True)
    assert admission.normalized.product_id == "onion"
