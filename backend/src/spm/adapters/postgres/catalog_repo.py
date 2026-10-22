"""``PgCatalogReader``: catalog reads (areas, products + aliases, shops)."""

from geoalchemy2 import Geometry
from sqlalchemy import ColumnElement, Select, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from spm.adapters.postgres.tables import areas, product_aliases, products, shops
from spm.core.enums import BaseUnit
from spm.core.models import Area, GeoPoint, Product, Shop


def _lat(col: ColumnElement[object]) -> ColumnElement[float]:
    return func.ST_Y(cast(col, Geometry))


def _lng(col: ColumnElement[object]) -> ColumnElement[float]:
    return func.ST_X(cast(col, Geometry))


class PgCatalogReader:
    """Each call uses its own short session; the catalog is read rarely (``CatalogCache``)."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def products(self) -> list[Product]:
        return await self._products(None)

    async def product(self, product_id: str) -> Product | None:
        found = await self._products(product_id)
        return found[0] if found else None

    async def _products(self, product_id: str | None) -> list[Product]:
        query = select(products).order_by(products.c.sort_order, products.c.id)
        alias_query = select(product_aliases.c.product_id, product_aliases.c.alias).order_by(
            product_aliases.c.alias
        )
        if product_id is not None:
            query = query.where(products.c.id == product_id)
            alias_query = alias_query.where(product_aliases.c.product_id == product_id)
        async with self._sessions() as session:
            rows = (await session.execute(query)).mappings().all()
            alias_rows = (await session.execute(alias_query)).all()
        aliases: dict[str, list[str]] = {}
        for pid, alias in alias_rows:
            aliases.setdefault(pid, []).append(alias)
        return [
            Product(
                id=r["id"],
                name=r["name"],
                base_unit=BaseUnit(r["base_unit"]),
                category=r["category"],
                half_life_h=r["half_life_h"],
                abs_min=r["abs_min"],
                abs_max=r["abs_max"],
                active=r["active"],
                aliases=tuple(aliases.get(r["id"], ())),
            )
            for r in rows
        ]

    async def alias_map(self) -> dict[str, str]:
        async with self._sessions() as session:
            rows = await session.execute(
                select(product_aliases.c.alias, product_aliases.c.product_id)
            )
            return {alias: pid for alias, pid in rows.all()}

    async def areas(self) -> list[Area]:
        query = select(
            areas.c.id,
            areas.c.name,
            areas.c.timezone,
            _lat(areas.c.center).label("lat"),
            _lng(areas.c.center).label("lng"),
            areas.c.quiet_start,
            areas.c.quiet_end,
        ).order_by(areas.c.id)
        async with self._sessions() as session:
            rows = (await session.execute(query)).mappings().all()
        return [
            Area(
                r["id"],
                r["name"],
                r["timezone"],
                GeoPoint(r["lat"], r["lng"]),
                r["quiet_start"],
                r["quiet_end"],
            )
            for r in rows
        ]

    async def shops(self, area_id: str | None = None) -> list[Shop]:
        query = self._shop_query()
        if area_id is not None:
            query = query.where(shops.c.area_id == area_id)
        return await self._shops(query)

    async def shop(self, shop_id: str) -> Shop | None:
        found = await self._shops(self._shop_query().where(shops.c.id == shop_id))
        return found[0] if found else None

    @staticmethod
    def _shop_query() -> Select[tuple[object, ...]]:
        return select(
            shops.c.id,
            shops.c.name,
            shops.c.area_id,
            _lat(shops.c.location).label("lat"),
            _lng(shops.c.location).label("lng"),
            shops.c.address,
            shops.c.active,
        ).order_by(shops.c.id)

    async def _shops(self, query: Select[tuple[object, ...]]) -> list[Shop]:
        async with self._sessions() as session:
            rows = (await session.execute(query)).mappings().all()
        return [
            Shop(
                r["id"],
                r["name"],
                r["area_id"],
                GeoPoint(r["lat"], r["lng"]),
                r["address"],
                r["active"],
            )
            for r in rows
        ]
