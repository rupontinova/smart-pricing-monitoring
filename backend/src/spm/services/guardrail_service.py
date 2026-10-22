"""``GuardrailService``: every synchronous check a report must pass before it is stored.

Checks run cheapest and most informative first, and the first failure raises:

1. rate limits (hour, then day)       4. shop exists and is active
2. normalize (M05)                    5. absolute price bounds (with "did you mean" hints)
3. observed_at freshness              6. geo-fence
                                      7. duplicates (DB lookup; reported, not raised)
"""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from spm.constants import DEDUPE_WINDOW_H, RATE_LIMIT_PER_DAY, RATE_LIMIT_PER_HOUR
from spm.core.errors import QuantityOutOfRange, RateLimited, UnknownShop
from spm.core.guardrails import (
    bounds_reference,
    check_absolute_bounds,
    check_observed_at,
    geofence,
    hints_extra,
    is_duplicate,
    unit_error_hints,
)
from spm.core.models import FairBand, LocationFix, NormalizedPrice, RawPriceInput, Shop
from spm.core.normalizer import normalize, resolve_product
from spm.ports import Clock, RateLimiter
from spm.services.catalog_cache import CatalogCache

RATE_LIMITS: tuple[tuple[int, int, str], ...] = (
    (RATE_LIMIT_PER_HOUR, 3600, "hour"),
    (RATE_LIMIT_PER_DAY, 86_400, "day"),
)


class RecentDuplicateFinder(Protocol):
    async def find_recent_duplicate(
        self, reporter_id: UUID, product_id: str, shop_id: str, since: datetime
    ) -> list[tuple[UUID, Decimal]]: ...


class ReportsUnitOfWork(Protocol):
    """The slice of ``UnitOfWork`` the guardrails use."""

    @property
    def reports(self) -> RecentDuplicateFinder: ...


class BandLookup(Protocol):
    """The slice of ``HotCache`` the guardrails use (the band median ranks unit hints)."""

    async def get_band(self, area_id: str, product_id: str) -> FairBand | None: ...


@dataclass(frozen=True, slots=True)
class Admission:
    normalized: NormalizedPrice
    shop: Shop
    location_verified: bool
    distance_m: float | None
    duplicate_of: UUID | None  # an earlier, near-identical report by the same reporter


class GuardrailService:
    def __init__(
        self,
        catalog_cache: CatalogCache,
        rate_limiter: RateLimiter,
        uow_factory: Callable[[], AbstractAsyncContextManager[ReportsUnitOfWork]],
        clock: Clock,
        hot_cache: BandLookup | None = None,
    ) -> None:
        self._catalog = catalog_cache
        self._rate_limiter = rate_limiter
        self._uow_factory = uow_factory
        self._clock = clock
        self._hot_cache = hot_cache  # None until the band cache exists (M12)

    async def admit(
        self,
        reporter_id: UUID,
        raw: RawPriceInput,
        shop_id: str,
        observed_at: datetime,
        fix: LocationFix | None,
        *,
        allow_backfill: bool = False,
        enforce_rate_limits: bool = True,
    ) -> Admission:
        now = self._clock.now()

        # 1. Rate limits: one counter per window; a request denied hourly doesn't count daily.
        #    The trusted admin backfill (simulator) skips them.
        for limit, window_s, label in RATE_LIMITS if enforce_rate_limits else ():
            allowed, retry_after = await self._rate_limiter.hit(
                "report", str(reporter_id), limit, window_s
            )
            if not allowed:
                raise RateLimited(
                    f"Report limit reached ({limit} per {label}).",
                    retry_after_s=retry_after,
                    limit=limit,
                    window=label,
                )

        # 2. Normalize.
        catalog = await self._catalog.ensure_fresh()
        try:
            normalized = normalize(raw, catalog)
        except QuantityOutOfRange as exc:
            # 500 kg for the price of 500 g fails here, before the bounds check: hint it too.
            quoted = resolve_product(raw.product_id, raw.product_text, catalog)
            hints = unit_error_hints(raw, quoted, bounds_reference(quoted, None))
            exc.extra["hints"] = hints_extra(hints)
            raise
        product = catalog.product(normalized.product_id)
        assert product is not None  # normalize resolved it from this catalog

        # 3. Freshness.
        check_observed_at(observed_at, now, allow_backfill=allow_backfill)

        # 4. Shop.
        shop = catalog.shop(shop_id)
        if shop is None or not shop.active:
            raise UnknownShop(f"Unknown shop {shop_id!r}.", shop_id=shop_id)

        # 5. Absolute bounds; the cached band median (when there is one) ranks the hints.
        band = await self._hot_cache.get_band(shop.area_id, product.id) if self._hot_cache else None
        check_absolute_bounds(normalized, product, band.p50 if band else None)

        # 6. Geo-fence.
        location_verified, distance_m = geofence(shop, fix)

        # 7. Duplicates.
        since = observed_at - timedelta(hours=DEDUPE_WINDOW_H)
        async with self._uow_factory() as uow:
            recent = await uow.reports.find_recent_duplicate(
                reporter_id, product.id, shop.id, since
            )
        duplicate_of = next(
            (rid for rid, price in recent if is_duplicate(normalized.norm_price, [price])), None
        )
        return Admission(normalized, shop, location_verified, distance_m, duplicate_of)
