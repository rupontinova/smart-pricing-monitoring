"""Stand-in for the parts of M17's nightly ``maintenance`` job that change what scoring sees.

Without them a replay drifts from production: a MARKET_SHIFT anomaly opened on day 5 would stay
open (and dedup-block every later shift of that product), and PENDING reports would never expire.
Replace with the real job once M17 lands. Rules (MODULES.md M17, maintenance steps 2 and 3):

- PENDING older than its corroboration window → EXPIRED.
- Anomalies older than ``ANOMALY_AUTO_RESOLVE_D`` → resolved; a resolved MARKET_SHIFT clears the
  band's ``rebase_at``.

(The SHOP_OUTLIER "back within |z_market| ≤ 2" resolution is left to M17.)
"""

from datetime import datetime, timedelta

from sqlalchemy import and_, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from spm.adapters.postgres.tables import anomalies, band_state, report_assessments
from spm.constants import ANOMALY_AUTO_RESOLVE_D
from spm.core.enums import AnomalyKind, Classification
from spm.core.trust import corroboration_window_h


async def nightly(
    sf: async_sessionmaker[AsyncSession], now: datetime, half_lives: dict[str, int]
) -> None:
    async with sf() as session, session.begin():
        for product, half_life_h in half_lives.items():
            window = timedelta(hours=corroboration_window_h(half_life_h))
            await session.execute(
                update(report_assessments)
                .where(
                    report_assessments.c.product_id == product,
                    report_assessments.c.classification == Classification.PENDING.value,
                    report_assessments.c.observed_at < now - window,
                )
                .values(classification=Classification.EXPIRED.value, updated_at=now)
            )
        resolved = await session.execute(
            update(anomalies)
            .where(
                anomalies.c.status == "open",
                anomalies.c.detected_at < now - timedelta(days=ANOMALY_AUTO_RESOLVE_D),
            )
            .values(status="resolved", resolved_at=now)
            .returning(anomalies.c.kind, anomalies.c.product_id, anomalies.c.area_id)
        )
        for kind, product, area in resolved.all():
            if kind == AnomalyKind.MARKET_SHIFT.value:
                await session.execute(
                    update(band_state)
                    .where(and_(band_state.c.product_id == product, band_state.c.area_id == area))
                    .values(rebase_at=None)
                )
