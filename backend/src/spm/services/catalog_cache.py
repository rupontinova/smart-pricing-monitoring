"""``CatalogCache``: the whole catalog in memory, with synchronous getters.

The normalizer and guardrails need fast synchronous lookups, so the async ``CatalogReader`` is read
in bulk and kept for ``CATALOG_REFRESH_S``. Refresh is lazy: callers ``await ensure_fresh()`` (the
API does this in a dependency) and then use the sync getters.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Self

import structlog

from spm.constants import CATALOG_REFRESH_S
from spm.core.models import Area, Product, Shop
from spm.ports import CatalogReader, Clock

log = structlog.get_logger("spm.catalog")


@dataclass(frozen=True, slots=True)
class _Snapshot:
    loaded_at: datetime
    products: tuple[Product, ...]
    areas: tuple[Area, ...]
    shops: tuple[Shop, ...]
    aliases: dict[str, str]
    products_by_id: dict[str, Product] = field(init=False)
    areas_by_id: dict[str, Area] = field(init=False)
    shops_by_id: dict[str, Shop] = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "products_by_id", {p.id: p for p in self.products})
        object.__setattr__(self, "areas_by_id", {a.id: a for a in self.areas})
        object.__setattr__(self, "shops_by_id", {s.id: s for s in self.shops})


class CatalogCache:
    def __init__(
        self, reader: CatalogReader, clock: Clock, refresh_s: float = CATALOG_REFRESH_S
    ) -> None:
        self._reader = reader
        self._clock = clock
        self._refresh_s = refresh_s
        self._snapshot: _Snapshot | None = None
        self._lock = asyncio.Lock()

    @property
    def loaded_at(self) -> datetime | None:
        return self._snapshot.loaded_at if self._snapshot else None

    def _is_fresh(self) -> bool:
        snap = self._snapshot
        return snap is not None and (
            (self._clock.now() - snap.loaded_at).total_seconds() < self._refresh_s
        )

    async def ensure_fresh(self) -> Self:
        """Load the catalog if it was never loaded or is at least ``refresh_s`` old.

        If a refresh fails while an older snapshot exists, the old one keeps being served (and the
        next call retries); with no snapshot at all, the error propagates.
        """
        if self._is_fresh():
            return self
        async with self._lock:
            if self._is_fresh():  # another task refreshed while we waited
                return self
            try:
                await self.refresh()
            except Exception:
                if self._snapshot is None:
                    raise
                log.warning("catalog_refresh_failed_serving_stale", exc_info=True)
        return self

    async def refresh(self) -> None:
        products = await self._reader.products()
        areas = await self._reader.areas()
        shops = await self._reader.shops()
        aliases = await self._reader.alias_map()
        self._snapshot = _Snapshot(
            loaded_at=self._clock.now(),
            products=tuple(products),
            areas=tuple(areas),
            shops=tuple(shops),
            aliases=dict(aliases),
        )
        log.info("catalog_loaded", products=len(products), areas=len(areas), shops=len(shops))

    @property
    def _snap(self) -> _Snapshot:
        if self._snapshot is None:
            raise RuntimeError("CatalogCache used before ensure_fresh()")
        return self._snapshot

    # Synchronous getters ------------------------------------------------------------------
    def product(self, product_id: str) -> Product | None:
        return self._snap.products_by_id.get(product_id)

    def products(self) -> list[Product]:
        """All products (including inactive), in display order."""
        return list(self._snap.products)

    def alias_map(self) -> dict[str, str]:
        """Normalized alias -> product_id."""
        return self._snap.aliases

    def area(self, area_id: str) -> Area | None:
        return self._snap.areas_by_id.get(area_id)

    def areas(self) -> list[Area]:
        return list(self._snap.areas)

    def shop(self, shop_id: str) -> Shop | None:
        return self._snap.shops_by_id.get(shop_id)

    def shops(self) -> list[Shop]:
        return list(self._snap.shops)

    def shops_in_area(self, area_id: str) -> list[Shop]:
        """Active shops in the area."""
        return [s for s in self._snap.shops if s.area_id == area_id and s.active]
