"""``PgBandRepo``: persisted band state (``band_state``) and shop prices (``shop_prices``)."""

from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

from sqlalchemy import delete, func, literal, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from spm.adapters.postgres.tables import band_state, products, shop_prices
from spm.core.enums import Confidence
from spm.core.models import FairBand, ShopPrice

_PRICE_QUANTUM = Decimal("0.0001")  # numeric(14,4)


def _money(x: float | None) -> Decimal | None:
    """Stats run on float; stored prices are Decimal rounded half-even to 4 dp (§2.4)."""
    return None if x is None else Decimal(str(x)).quantize(_PRICE_QUANTUM, rounding=ROUND_HALF_EVEN)


def _float(x: Any) -> float | None:
    return None if x is None else float(x)


class PgBandRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------------- band
    async def get_band(self, product_id: str, area_id: str) -> FairBand | None:
        row = (
            await self._session.execute(
                select(band_state).where(
                    band_state.c.product_id == product_id, band_state.c.area_id == area_id
                )
            )
        ).first()
        if row is None:
            return None
        return FairBand(
            product_id=row.product_id,
            area_id=row.area_id,
            p25=_float(row.p25),
            p50=_float(row.p50),
            p75=_float(row.p75),
            mad=_float(row.mad),
            total_weight=float(row.total_weight),
            n_eff=float(row.n_eff),
            n_shops=row.n_shops,
            confidence=Confidence(row.confidence),
            newest_obs_at=row.newest_obs_at,
            mean_age_h=_float(row.mean_age_h),
            half_life_h=row.half_life_h,
            rebase_at=row.rebase_at,
            computed_at=row.computed_at,
        )

    async def upsert_band(self, band: FairBand) -> None:
        values = {
            "product_id": band.product_id,
            "area_id": band.area_id,
            "p25": _money(band.p25),
            "p50": _money(band.p50),
            "p75": _money(band.p75),
            "mad": _money(band.mad),
            "total_weight": band.total_weight,
            "n_eff": band.n_eff,
            "n_shops": band.n_shops,
            "confidence": band.confidence.value,
            "newest_obs_at": band.newest_obs_at,
            "mean_age_h": band.mean_age_h,
            "half_life_h": band.half_life_h,
            "rebase_at": band.rebase_at,
            "computed_at": band.computed_at,
        }
        stmt = insert(band_state).values(**values)
        await self._session.execute(
            stmt.on_conflict_do_update(
                index_elements=[band_state.c.product_id, band_state.c.area_id],
                set_={k: stmt.excluded[k] for k in values if k not in ("product_id", "area_id")},
            )
        )

    async def get_rebase(self, product_id: str, area_id: str) -> datetime | None:
        return (
            await self._session.execute(
                select(band_state.c.rebase_at).where(
                    band_state.c.product_id == product_id, band_state.c.area_id == area_id
                )
            )
        ).scalar_one_or_none()

    async def set_rebase(self, product_id: str, area_id: str, at: datetime) -> None:
        """Start the band window at ``at`` (after a MARKET_SHIFT). Creates the row if needed."""
        source = select(
            literal(product_id), literal(area_id), products.c.half_life_h, literal(at), literal(at)
        ).where(products.c.id == product_id)
        stmt = insert(band_state).from_select(
            ["product_id", "area_id", "half_life_h", "computed_at", "rebase_at"], source
        )
        await self._session.execute(
            stmt.on_conflict_do_update(
                index_elements=[band_state.c.product_id, band_state.c.area_id],
                set_={"rebase_at": stmt.excluded.rebase_at},
            )
        )

    # ------------------------------------------------------------------- shop prices
    @staticmethod
    def _shop_price_row(sp: ShopPrice) -> dict[str, Any]:
        return {
            "product_id": sp.product_id,
            "shop_id": sp.shop_id,
            "area_id": sp.area_id,
            "price": _money(sp.price),
            "verified": sp.verified,
            "support_weight": sp.support_weight,
            "n_reports": sp.n_reports,
            "last_observed_at": sp.last_observed_at,
            "verified_at": sp.verified_at,
            "updated_at": func.now(),
        }

    async def upsert_shop_price(self, sp: ShopPrice) -> None:
        row = self._shop_price_row(sp)
        stmt = insert(shop_prices).values(**row)
        await self._session.execute(
            stmt.on_conflict_do_update(
                index_elements=[shop_prices.c.product_id, shop_prices.c.shop_id],
                set_={k: stmt.excluded[k] for k in row if k not in ("product_id", "shop_id")},
            )
        )

    async def replace_shop_prices(
        self, product_id: str, area_id: str, prices: list[ShopPrice]
    ) -> None:
        await self._session.execute(
            delete(shop_prices).where(
                shop_prices.c.product_id == product_id, shop_prices.c.area_id == area_id
            )
        )
        if prices:
            # One multi-row VALUES insert: unlike executemany, it can render now() per row.
            await self._session.execute(
                insert(shop_prices).values([self._shop_price_row(sp) for sp in prices])
            )

    async def shop_prices(self, product_id: str, area_id: str) -> list[ShopPrice]:
        rows = await self._session.execute(
            select(shop_prices)
            .where(shop_prices.c.product_id == product_id, shop_prices.c.area_id == area_id)
            .order_by(shop_prices.c.shop_id)
        )
        return [
            ShopPrice(
                product_id=r.product_id,
                shop_id=r.shop_id,
                area_id=r.area_id,
                price=float(r.price),
                verified=r.verified,
                support_weight=float(r.support_weight),
                n_reports=r.n_reports,
                last_observed_at=r.last_observed_at,
                verified_at=r.verified_at,
            )
            for r in rows.all()
        ]
