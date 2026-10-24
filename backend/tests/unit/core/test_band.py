import uuid
from datetime import timedelta

import pytest

from spm.core.band import (
    band_window_start,
    cache_ttl_s,
    compute_band,
    compute_shop_prices,
)
from spm.core.enums import Classification, Confidence
from spm.core.models import Observation
from tests.fixtures.scenarios import NOW, observation

C = Classification
HL = 48  # onion half-life (h)


def band(obs: list[Observation], rebase_at=None):  # type: ignore[no-untyped-def]
    return compute_band("onion", "demo-central", obs, NOW, HL, rebase_at)


def test_shop_outlier_excluded_from_band() -> None:
    obs = [observation(s, 80, hours_ago=1) for s in ("shop-a", "shop-b", "shop-c", "shop-d")]
    obs.append(observation("shop-e", 120, hours_ago=1, classification=C.SHOP_OUTLIER))
    result = band(obs)
    assert result.p50 == 80
    assert result.n_shops == 4


def test_dominant_shop_capped_keeps_four_shop_consensus() -> None:
    flood = [observation("shop-a", 100) for _ in range(20)]
    others = [observation(s, 80) for s in ("shop-b", "shop-c", "shop-d", "shop-e")]
    result = band(flood + others)
    assert result.p50 == 80  # uncapped, shop-a's 20 reports would put the median at 100
    assert result.total_weight == pytest.approx(24 * 0.6)  # uncapped total


def test_freshness_old_reports_fade() -> None:
    # The plan's case: reports 10 half-lives old at 60, fresh ones at 80.
    ancient = [observation("shop-a", 60, hours_ago=10 * HL) for _ in range(5)]
    fresh = [observation("shop-a", 80, hours_ago=1) for _ in range(5)]
    assert band(ancient + fresh).p50 == 80


def test_freshness_inside_the_window() -> None:
    # 3 half-lives old weighs 1/8: 60s from that long ago barely move a fresh market at 80.
    shops = ("shop-a", "shop-b", "shop-c")
    old = [observation(s, 60, hours_ago=3 * HL) for s in shops for _ in range(3)]
    fresh = [observation(s, 80, hours_ago=1) for s in shops for _ in range(3)]
    assert band(old + fresh).p50 == 80


def test_rebase_ignores_reports_before_it() -> None:
    before = [observation(s, 80, hours_ago=30) for s in ("shop-a", "shop-b", "shop-c")]
    after = [observation(s, 110, hours_ago=2) for s in ("shop-a", "shop-b", "shop-c")]
    rebase = NOW - timedelta(hours=10)
    result = band(before + after, rebase_at=rebase)
    assert result.p50 == 110
    assert result.rebase_at == rebase
    assert band(before + after).p50 != 110  # without the re-base the old prices count


def test_band_window_start() -> None:
    assert band_window_start(NOW, 48, None) == NOW - timedelta(hours=192)
    assert band_window_start(NOW, 504, None) == NOW - timedelta(days=60)  # capped
    later = NOW - timedelta(hours=5)
    assert band_window_start(NOW, 48, later) == later
    earlier = NOW - timedelta(days=30)
    assert band_window_start(NOW, 48, earlier) == NOW - timedelta(hours=192)


def _weighted(total: float, shops: int) -> list[Observation]:
    """Fresh, location-verified reports whose trust sums to ``total`` across ``shops`` shops."""
    per = total / shops
    return [observation(f"shop-{i}", 80, trust=per) for i in range(shops)]


@pytest.mark.parametrize(
    ("total", "shops", "expected"),
    [
        (3.0, 3, Confidence.HIGH),
        (3.0, 2, Confidence.MEDIUM),  # enough weight, too few shops
        (2.99, 3, Confidence.MEDIUM),
        (1.5, 1, Confidence.MEDIUM),
        (1.49, 1, Confidence.LOW),
        (0.5, 1, Confidence.LOW),
        (0.49, 1, Confidence.STALE),
    ],
)
def test_confidence_thresholds(total: float, shops: int, expected: Confidence) -> None:
    result = band(_weighted(total, shops))
    assert result.total_weight == pytest.approx(total)
    assert result.confidence is expected
    assert result.p50 == 80  # quantiles are filled even when STALE


def test_no_observations_is_stale_with_none_quantiles() -> None:
    result = band([])
    assert result.confidence is Confidence.STALE
    assert (result.p25, result.p50, result.p75, result.mad) == (None, None, None, None)
    assert result.newest_obs_at is None and result.mean_age_h is None
    assert result.computed_at == NOW


def test_band_summary_fields() -> None:
    obs = [observation("shop-a", 78, hours_ago=2), observation("shop-b", 82, hours_ago=6)]
    result = band(obs)
    p25, p50, p75 = result.p25, result.p50, result.p75
    assert p25 is not None and p50 is not None and p75 is not None
    assert p25 <= p50 <= p75
    assert result.newest_obs_at == NOW - timedelta(hours=2)
    assert result.mean_age_h is not None and 2 < result.mean_age_h < 6
    assert result.n_eff == pytest.approx(2.0, rel=0.1)
    assert result.mad is not None and result.mad > 0


def test_zero_trust_band_is_stale_but_described() -> None:
    result = band([observation("shop-a", 78, trust=0.0), observation("shop-b", 82, trust=0.0)])
    assert result.confidence is Confidence.STALE
    assert result.p50 == 80 and result.total_weight == 0 and result.n_shops == 0


# ---------------------------------------------------------------------------- shop prices
def shop_prices(by_shop: dict[str, list[Observation]], high=frozenset()):  # type: ignore[no-untyped-def]
    return compute_shop_prices("onion", "demo-central", by_shop, NOW, HL, lambda r: r in high)


def test_stale_shop_dropped() -> None:
    by_shop = {
        "shop-a": [observation("shop-a", 80, hours_ago=2 * HL + 1)],  # freshness < 0.25
        "shop-b": [observation("shop-b", 80, hours_ago=2 * HL - 1)],
    }
    assert [sp.shop_id for sp in shop_prices(by_shop)] == ["shop-b"]


def test_unverified_shop_keeps_latest_price() -> None:
    latest = observation("shop-a", 90, hours_ago=1)
    by_shop = {"shop-a": [observation("shop-a", 80, hours_ago=5), latest]}
    (sp,) = shop_prices(by_shop)
    assert (sp.price, sp.verified, sp.verified_at, sp.n_reports) == (90.0, False, None, 1)
    assert sp.last_observed_at == latest.observed_at


def test_two_independent_agreeing_reports_verified() -> None:
    a, b = observation("shop-a", 80, hours_ago=1), observation("shop-a", 81, hours_ago=3)
    (sp,) = shop_prices({"shop-a": [a, b]})
    assert sp.verified and 80 <= sp.price <= 81 and sp.n_reports == 2
    assert sp.verified_at == a.observed_at
    assert sp.support_weight > 0


def test_high_trust_single_report_verified() -> None:
    o = observation("shop-a", 80, hours_ago=1)
    (sp,) = shop_prices({"shop-a": [o]}, high={o.reporter_id})
    assert sp.verified


def test_shop_outlier_counts_for_shop_price_pending_does_not() -> None:
    outlier = observation("shop-a", 120, hours_ago=1, classification=C.SHOP_OUTLIER)
    pending = observation("shop-b", 120, hours_ago=1, classification=C.PENDING)
    prices = shop_prices({"shop-a": [outlier], "shop-b": [pending]})
    assert [(sp.shop_id, sp.price) for sp in prices] == [("shop-a", 120.0)]


def test_cache_ttl() -> None:
    assert cache_ttl_s(48) == 96 * 3600
    assert cache_ttl_s(6) == 24 * 3600  # floor of 24 h


def test_reporter_ids_are_distinct_by_default() -> None:
    assert observation("shop-a", 1).reporter_id != observation("shop-a", 1).reporter_id
    assert isinstance(observation("shop-a", 1).report_id, uuid.UUID)
