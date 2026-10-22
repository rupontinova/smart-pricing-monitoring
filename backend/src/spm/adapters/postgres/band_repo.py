"""``PgBandRepo``: ``shop_prices`` for report status (M08); the rest of ``BandRepo`` is M12."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from spm.adapters.postgres.tables import shop_prices
from spm.core.models import ShopPrice


class PgBandRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def shop_prices(self, product_id: str, area_id: str) -> list[ShopPrice]:
        rows = await self._session.execute(
            select(shop_prices).where(
                shop_prices.c.product_id == product_id, shop_prices.c.area_id == area_id
            )
        )
        return [
            ShopPrice(
                product_id=r["product_id"],
                shop_id=r["shop_id"],
                area_id=r["area_id"],
                price=float(r["price"]),
                verified=r["verified"],
                support_weight=float(r["support_weight"]),
                n_reports=r["n_reports"],
                last_observed_at=r["last_observed_at"],
                verified_at=r["verified_at"],
            )
            for r in rows.mappings().all()
        ]
