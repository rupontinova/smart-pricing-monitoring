"""Daily price statistics (``price_stats_daily``): one row per product × area (and per shop) per
area-local day. Written live by scoring (M13), finalized nightly (M17)."""

import math
from collections.abc import Collection, Sequence
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from spm.core.enums import Classification
from spm.core.models import DailyStat, Observation
from spm.core.stats import weighted_mad, weighted_quantile
from spm.core.trust import report_weight


def local_day(at: datetime, timezone: str) -> date:
    """The calendar day of ``at`` in the area's timezone."""
    return at.astimezone(ZoneInfo(timezone)).date()


def day_bounds(day: date, timezone: str) -> tuple[datetime, datetime]:
    """[start, end) of an area-local day, as aware datetimes."""
    tz = ZoneInfo(timezone)
    start = datetime.combine(day, time.min, tzinfo=tz)
    return start, datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz)


def compute_daily_stat(
    product_id: str,
    area_id: str,
    shop_id: str | None,
    obs: Sequence[Observation],
    day: date,
    timezone: str,
    now: datetime,
    half_life_h: int,
    included: Collection[Classification],
    *,
    final: bool = False,
) -> DailyStat | None:
    """Weighted quartiles of the day's included reports (for one shop, or the whole area).

    None when there is nothing to summarize.
    """
    start, end = day_bounds(day, timezone)
    used = [
        o for o in obs
        if start <= o.observed_at < end and o.classification in included
        and (shop_id is None or o.shop_id == shop_id)
    ]  # fmt: skip
    if not used:
        return None
    prices = [o.norm_price for o in used]
    weights = [
        report_weight(o.trust, max((now - o.observed_at).total_seconds() / 3600, 0.0),
                      half_life_h, o.location_verified)
        for o in used
    ]  # fmt: skip
    if math.fsum(weights) == 0:
        weights = [1.0] * len(used)
    p50 = weighted_quantile(prices, weights, 0.5)
    return DailyStat(
        product_id=product_id,
        area_id=area_id,
        shop_id=shop_id,
        day=day,
        p25=weighted_quantile(prices, weights, 0.25),
        p50=p50,
        p75=weighted_quantile(prices, weights, 0.75),
        mad=weighted_mad(prices, weights, p50),
        n_reports=len(used),
        total_weight=math.fsum(weights),
        final=final,
    )
