from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from spm.core.band import BAND_INCLUDED, SHOP_INCLUDED
from spm.core.enums import Classification
from spm.core.rollup import compute_daily_stat, day_bounds, local_day
from tests.fixtures.scenarios import NOW, observation

DHAKA = "Asia/Dhaka"  # UTC+6


def test_local_day_and_bounds() -> None:
    late_utc = datetime(2026, 10, 10, 19, 0, tzinfo=ZoneInfo("UTC"))  # 01:00 on the 11th in Dhaka
    assert local_day(late_utc, DHAKA) == date(2026, 10, 11)
    start, end = day_bounds(date(2026, 10, 11), DHAKA)
    assert start.utcoffset() == timedelta(hours=6)
    assert end - start == timedelta(days=1)
    assert start <= late_utc < end


def test_daily_stat_area_and_shop() -> None:
    day = local_day(NOW, DHAKA)
    obs = [
        observation("shop-a", 78, hours_ago=1),
        observation("shop-b", 82, hours_ago=2),
        observation("shop-c", 120, hours_ago=1, classification=Classification.SHOP_OUTLIER),
        observation("shop-a", 70, hours_ago=48),  # another day
    ]
    area = compute_daily_stat(
        "onion", "demo-central", None, obs, day, DHAKA, NOW, 48, BAND_INCLUDED
    )
    assert area is not None and area.n_reports == 2 and 78 <= area.p50 <= 82
    shop_c = compute_daily_stat(
        "onion", "demo-central", "shop-c", obs, day, DHAKA, NOW, 48, SHOP_INCLUDED
    )
    assert shop_c is not None and shop_c.p50 == 120 and not shop_c.final
    assert (
        compute_daily_stat(
            "onion", "demo-central", "shop-z", obs, day, DHAKA, NOW, 48, SHOP_INCLUDED
        )
        is None
    )


def test_daily_stat_zero_trust_falls_back_to_unweighted() -> None:
    day = local_day(NOW, DHAKA)
    obs = [observation("shop-a", 78, hours_ago=1, trust=0.0), observation("shop-b", 82, trust=0.0)]
    stat = compute_daily_stat(
        "onion", "demo-central", None, obs, day, DHAKA, NOW, 48, BAND_INCLUDED, final=True
    )
    assert stat is not None and stat.p50 == 80 and stat.total_weight == 2 and stat.final
