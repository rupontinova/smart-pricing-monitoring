"""Idempotent catalog seeding (``spm seed``)."""

from dataclasses import dataclass

from sqlalchemy import case, func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql.elements import ColumnElement

from spm.adapters.catalog_yaml import SeedCatalog
from spm.adapters.postgres.catalog_repo import PgCatalogReader
from spm.adapters.postgres.tables import areas, product_aliases, products, shops
from spm.core.models import GeoPoint
from spm.ports import ShopGeoIndex


@dataclass(frozen=True, slots=True)
class SeedReport:
    areas: int
    products: int
    aliases: int
    shops: int
    geo_shops: int


def _geog(point: GeoPoint) -> ColumnElement[object]:
    return func.ST_GeogFromText(f"SRID=4326;POINT({point.lng!r} {point.lat!r})")


async def upsert_catalog(session: AsyncSession, seed: SeedCatalog) -> None:
    """Insert or update every seed row. Doesn't commit.

    ``products.half_life_h`` is only overwritten while it still equals the previous default:
    once M17 has tuned it, the tuned value is kept.
    """
    for a in seed.areas:
        stmt = insert(areas).values(
            id=a.id,
            name=a.name,
            timezone=a.timezone,
            center=_geog(a.center),
            quiet_start=a.quiet_start,
            quiet_end=a.quiet_end,
        )
        ex = stmt.excluded
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=[areas.c.id],
                set_={
                    "name": ex.name,
                    "timezone": ex.timezone,
                    "center": ex.center,
                    "quiet_start": ex.quiet_start,
                    "quiet_end": ex.quiet_end,
                },
            )
        )

    for order, p in enumerate(seed.products):
        stmt = insert(products).values(
            id=p.id,
            name=p.name,
            base_unit=p.base_unit,
            category=p.category,
            default_half_life_h=p.half_life_h,
            half_life_h=p.half_life_h,
            abs_min=p.abs_min,
            abs_max=p.abs_max,
            active=p.active,
            sort_order=order,
        )
        ex = stmt.excluded
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=[products.c.id],
                set_={
                    "name": ex.name,
                    "base_unit": ex.base_unit,
                    "category": ex.category,
                    "abs_min": ex.abs_min,
                    "abs_max": ex.abs_max,
                    "active": ex.active,
                    "sort_order": ex.sort_order,
                    "default_half_life_h": ex.default_half_life_h,
                    "half_life_h": case(
                        (products.c.half_life_h == products.c.default_half_life_h, ex.half_life_h),
                        else_=products.c.half_life_h,
                    ),
                },
            )
        )

    alias_rows = [{"alias": alias, "product_id": pid} for alias, pid in seed.alias_map().items()]
    if alias_rows:
        stmt = insert(product_aliases).values(alias_rows)
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=[product_aliases.c.alias],
                set_={"product_id": stmt.excluded.product_id},
            )
        )

    for s in seed.shops:
        stmt = insert(shops).values(
            id=s.id,
            name=s.name,
            area_id=s.area_id,
            location=_geog(s.location),
            address=s.address,
            active=s.active,
        )
        ex = stmt.excluded
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=[shops.c.id],
                set_={
                    "name": ex.name,
                    "area_id": ex.area_id,
                    "location": ex.location,
                    "address": ex.address,
                    "active": ex.active,
                },
            )
        )


async def seed_catalog(
    seed: SeedCatalog,
    session_factory: async_sessionmaker[AsyncSession],
    geo_index: ShopGeoIndex,
) -> SeedReport:
    """Upsert the catalog in one transaction, then rebuild the shop geo index from the database."""
    async with session_factory() as session:
        await upsert_catalog(session, seed)
        await session.commit()
    db_shops = await PgCatalogReader(session_factory).shops()
    await geo_index.load_shop_geo(db_shops)
    return SeedReport(
        areas=len(seed.areas),
        products=len(seed.products),
        aliases=len(seed.alias_map()),
        shops=len(seed.shops),
        geo_shops=sum(1 for s in db_shops if s.active),
    )
