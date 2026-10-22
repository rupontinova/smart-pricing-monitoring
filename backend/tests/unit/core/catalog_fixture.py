"""The repo's seed catalog as a loaded ``CatalogCache`` (shared by core tests)."""

import asyncio
from datetime import UTC, datetime

from spm.adapters.memory import FixedClock, MemoryCatalogReader
from spm.core.normalizer import ProductCatalog
from spm.services.catalog_cache import CatalogCache


def load_seed_catalog() -> CatalogCache:
    cache = CatalogCache(
        MemoryCatalogReader.from_seeds(), FixedClock(datetime(2026, 10, 10, tzinfo=UTC))
    )
    return asyncio.run(cache.ensure_fresh())


# Built once at import: hypothesis tests can't use function-scoped fixtures.
CATALOG: ProductCatalog = load_seed_catalog()  # CatalogCache satisfies the normalizer's protocol
