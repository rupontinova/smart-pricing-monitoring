from pathlib import Path

from spm.adapters.catalog_yaml import SEEDS_DIR, SeedCatalog, load_catalog
from spm.core.models import Area, Product, Shop


class MemoryCatalogReader:
    """``CatalogReader`` over a ``SeedCatalog`` (by default the repo's seed files)."""

    def __init__(self, catalog: SeedCatalog) -> None:
        self.catalog = catalog
        self.loads = 0  # number of products() calls, for cache tests

    @classmethod
    def from_seeds(cls, seeds_dir: Path = SEEDS_DIR) -> "MemoryCatalogReader":
        return cls(load_catalog(seeds_dir))

    async def products(self) -> list[Product]:
        self.loads += 1
        return list(self.catalog.products)

    async def product(self, product_id: str) -> Product | None:
        return next((p for p in self.catalog.products if p.id == product_id), None)

    async def alias_map(self) -> dict[str, str]:
        return self.catalog.alias_map()

    async def areas(self) -> list[Area]:
        return list(self.catalog.areas)

    async def shops(self, area_id: str | None = None) -> list[Shop]:
        return [s for s in self.catalog.shops if area_id is None or s.area_id == area_id]

    async def shop(self, shop_id: str) -> Shop | None:
        return next((s for s in self.catalog.shops if s.id == shop_id), None)
