"""``BandService``: recompute a product × area band and shop prices, persist them, and keep the
hot cache filled. Triggered by scoring (M13) and the nightly job (M17); read by ``/check`` (M15).
"""

from collections import defaultdict
from collections.abc import Callable, Iterable
from contextlib import AbstractAsyncContextManager
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from spm.constants import SHOP_PRICE_MIN_FRESHNESS
from spm.core.band import band_window_start, cache_ttl_s, compute_band, compute_shop_prices
from spm.core.models import FairBand, Observation, Product, ShopPrice
from spm.core.stats import freshness_weight
from spm.ports import BandRepo, Clock, HotCache


class _AreaObservations(Protocol):
    async def observations_for_area(
        self, product_id: str, area_id: str, since: datetime, exclude_shop: str | None = None
    ) -> list[Observation]: ...


class _HighTrust(Protocol):
    async def high_trust_ids(self, ids: Iterable[UUID]) -> set[UUID]: ...


class BandUnitOfWork(Protocol):
    @property
    def bands(self) -> BandRepo: ...
    @property
    def assessments(self) -> _AreaObservations: ...
    @property
    def reporters(self) -> _HighTrust: ...


class BandService:
    def __init__(
        self,
        hot_cache: HotCache,
        uow_factory: Callable[[], AbstractAsyncContextManager[BandUnitOfWork]],
        clock: Clock,
    ) -> None:
        self._hot = hot_cache
        self._uow_factory = uow_factory
        self._clock = clock

    async def recompute(
        self, uow: BandUnitOfWork, product: Product, area_id: str, now: datetime
    ) -> tuple[FairBand, list[ShopPrice]]:
        """Compute and upsert ``band_state`` and ``shop_prices`` inside ``uow`` (caller commits).

        One query loads the observations: the band window, widened to 2 half-lives so the shop
        prices are covered too (a re-base narrows the band but not the shop prices).
        """
        half_life = product.half_life_h
        rebase_at = await uow.bands.get_rebase(product.id, area_id)
        since = min(
            band_window_start(now, half_life, rebase_at), now - timedelta(hours=2 * half_life)
        )
        observations = await uow.assessments.observations_for_area(product.id, area_id, since)

        band = compute_band(product.id, area_id, observations, now, half_life, rebase_at)
        by_shop: dict[str, list[Observation]] = defaultdict(list)
        for o in observations:
            by_shop[o.shop_id].append(o)
        high_trust = await uow.reporters.high_trust_ids({o.reporter_id for o in observations})
        prices = compute_shop_prices(
            product.id, area_id, by_shop, now, half_life, lambda rid: rid in high_trust
        )

        await uow.bands.upsert_band(band)
        await uow.bands.replace_shop_prices(product.id, area_id, prices)
        return band, prices

    async def publish_to_cache(self, band: FairBand, prices: list[ShopPrice]) -> bool:
        """Write band and shop prices to the hot cache after the DB commit; a newer cached band
        is never overwritten. Returns whether this snapshot was written."""
        return await self._hot.put_snapshot(band, prices, cache_ttl_s(band.half_life_h))

    async def get_or_rebuild(self, area_id: str, product_id: str) -> FairBand | None:
        """The cached band; on a miss, reload band and shop prices from Postgres into the cache."""
        cached = await self._hot.get_band(area_id, product_id)
        if cached is not None:
            return cached
        async with self._uow_factory() as uow:
            band = await uow.bands.get_band(product_id, area_id)
            prices = await uow.bands.shop_prices(product_id, area_id)
        if band is None:
            return None
        now = self._clock.now()

        def fresh_enough(sp: ShopPrice) -> bool:
            age_h = (now - sp.last_observed_at).total_seconds() / 3600
            return freshness_weight(age_h, band.half_life_h) >= SHOP_PRICE_MIN_FRESHNESS

        fresh = [sp for sp in prices if fresh_enough(sp)]
        await self.publish_to_cache(band, fresh)
        return band
