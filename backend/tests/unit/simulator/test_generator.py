"""The simulator's market model and report generator (pure: no Docker)."""

import itertools
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from simulator.generator import Market, SimReport, Truth, generate
from simulator.scenario import (
    Honest,
    Malicious,
    Reporters,
    Scenario,
    Sloppy,
    Spammer,
    load_scenario,
    scenario_names,
)
from spm.adapters.catalog_yaml import load_catalog
from spm.core.geo import haversine_m
from spm.core.models import GeoPoint
from spm.core.units import UNITS, parse_unit

CATALOG = load_catalog()
TZ = ZoneInfo("Asia/Dhaka")
SHOPS = {s.id: s.location for s in CATALOG.shops}
PREMIUM = {"shop-a": 0.0, "shop-b": -0.03, "shop-c": 0.02, "shop-d": 0.0, "shop-e": 0.04}


def scenario(**changes: Any) -> Scenario:
    base: dict[str, Any] = {
        "name": "t",
        "days": 10,
        "seed": 1,
        "products": ["onion", "eggs"],
        "true_price": {"onion": 80, "eggs": 150},
        "shop_premium": PREMIUM,
        "reporters": Reporters(honest=Honest(count=10)),
    }
    return Scenario.model_validate(base | changes)


def norm_price(r: SimReport) -> float:
    """What the normalizer will compute: price per base unit."""
    factor = UNITS[parse_unit(r.unit)][1]
    return float(r.price) / float(r.quantity * factor)


def test_default_scenarios_load() -> None:
    names = scenario_names()
    assert {
        "baseline_quiet",
        "onion_shock",
        "egg_gouging",
        "unit_typos",
        "spam_attack",
        "cold_start",
    } <= set(names)
    for name in names:
        assert load_scenario(name).name == name


def test_scenario_rejects_events_on_unknown_products_or_shops() -> None:
    with pytest.raises(ValidationError, match="not in products"):
        scenario(events=[{"type": "market_shock", "day": 1, "product": "rice", "multiplier": 2}])
    with pytest.raises(ValidationError, match="not in shop_premium"):
        scenario(
            events=[
                {
                    "type": "shop_gouging",
                    "day": 1,
                    "product": "eggs",
                    "shop": "shop-z",
                    "multiplier": 1.3,
                    "duration_days": 2,
                }
            ]
        )


def test_generation_is_deterministic_per_seed() -> None:
    s = scenario()
    first, again = generate(s, CATALOG).reports, generate(s, CATALOG).reports
    assert first == again
    other = generate(s.model_copy(update={"seed": 2}), CATALOG).reports
    assert [r.price for r in other] != [r.price for r in first]


def test_reports_are_ordered_by_submission_and_numbered() -> None:
    stream = generate(scenario(), CATALOG)
    reports = stream.reports
    assert reports, "10 reporters × 0.6/day × 10 days"
    assert [r.seq for r in reports] == list(range(len(reports)))
    assert all(a.submitted_at <= b.submitted_at for a, b in itertools.pairwise(reports))
    for r in reports:
        assert stream.start <= r.observed_at < r.submitted_at <= stream.end
        assert r.observed_at.utcoffset() == timedelta(0)
        local = r.observed_at.astimezone(TZ).time()
        assert time(8) <= local <= time(22)  # shopping hours, area-local


def test_market_shock_ramps_then_persists() -> None:
    s = scenario(
        daily_volatility={},
        events=[{"type": "market_shock", "day": 2, "product": "onion", "multiplier": 1.4}],
    )
    m = Market(s, TZ)
    start = m.origin + timedelta(days=2)  # local midnight
    assert start == datetime(2026, 1, 7, tzinfo=TZ)
    assert m.shock("onion", start) == 1.0
    assert m.shock("onion", start + timedelta(hours=12)) == pytest.approx(1.2)
    assert m.shock("onion", start + timedelta(days=5)) == pytest.approx(1.4)
    assert m.shock("eggs", start + timedelta(days=5)) == 1.0
    assert m.fair_price("onion", start + timedelta(days=5)) == pytest.approx(80 * 1.4)


def test_gouging_hits_one_shop_inside_its_window_only() -> None:
    s = scenario(
        events=[
            {
                "type": "shop_gouging",
                "day": 3,
                "product": "eggs",
                "shop": "shop-c",
                "multiplier": 1.3,
                "duration_days": 2,
            }
        ]
    )
    m = Market(s, TZ)
    start = datetime(2026, 1, 8, 8, tzinfo=TZ)  # day 3, when the shop opens
    assert m.gouging("eggs", "shop-c", start - timedelta(minutes=1)) == 1.0
    assert m.gouging("eggs", "shop-c", start + timedelta(hours=1)) == 1.3
    assert m.gouging("eggs", "shop-c", start + timedelta(days=2, hours=1)) == 1.0
    assert m.gouging("eggs", "shop-a", start + timedelta(hours=1)) == 1.0
    # The fair price ignores gouging: it is the median shop without it (shop-a/d at premium 0).
    t = start + timedelta(hours=1)
    assert m.fair_price("eggs", t) == pytest.approx(m.base("eggs", t))


def test_honest_reports_normalize_to_the_true_price() -> None:
    s = scenario(
        reporters=Reporters(honest=Honest(count=40)),
        events=[
            {
                "type": "shop_gouging",
                "day": 3,
                "product": "eggs",
                "shop": "shop-c",
                "multiplier": 1.3,
                "duration_days": 2,
            }
        ],
    )
    reports = generate(s, CATALOG).reports
    for r in reports:
        assert r.truth in (Truth.NORMAL, Truth.SHOP_OUTLIER)
        assert norm_price(r) == pytest.approx(r.true_price, rel=0.12)  # noise σ 2%, 6σ
    gouged = [r for r in reports if r.truth is Truth.SHOP_OUTLIER]
    assert gouged and all(r.shop == "shop-c" and r.product == "eggs" for r in gouged)


@pytest.mark.parametrize(
    ("mode", "kg_factor", "dozen_factor"),
    [("half", 0.5, 0.5), ("double", 2.0, 2.0), ("magnitude", 1000.0, 1 / 12)],
)
def test_unit_errors_scale_the_normalized_price(
    mode: str, kg_factor: float, dozen_factor: float
) -> None:
    sloppy = Sloppy(count=10, noise_sigma=0.0, unit_error_rate=1.0, unit_error_modes={mode: 1.0})
    s = scenario(reporters=Reporters(honest=Honest(count=0), sloppy=sloppy))
    reports = generate(s, CATALOG).reports
    assert {r.truth for r in reports} == {Truth.BAD_UNIT}
    for r in reports:
        factor = kg_factor if r.product == "onion" else dozen_factor
        assert norm_price(r) == pytest.approx(r.true_price * factor, rel=0.01), r


def test_malicious_reports_inflate_the_target_shop() -> None:
    s = scenario(
        reporters=Reporters(
            honest=Honest(count=0), malicious=Malicious(count=3, inflate=0.6, target_shop="shop-a")
        )
    )
    reports = generate(s, CATALOG).reports
    assert reports and {r.shop for r in reports} == {"shop-a"}
    for r in reports:
        assert r.truth is Truth.MALICIOUS and r.location is None
        assert norm_price(r) == pytest.approx(r.true_price * 1.6, rel=0.01)


def test_spammer_bursts_within_the_hour() -> None:
    spam = Spammer(count=1, day=4, hours=1, reports_per_hour=200, shop="shop-b", product="onion")
    s = scenario(reporters=Reporters(honest=Honest(count=0), spammer=spam))
    reports = generate(s, CATALOG).reports
    assert len(reports) == 200
    assert {r.device_id for r in reports} == {"sim-spammer-000"}
    first = datetime(2026, 1, 9, 10, tzinfo=TZ)  # day 4, 10:00 local by default
    assert all(first <= r.observed_at < first + timedelta(hours=1) for r in reports)


def test_gps_fixes_fall_within_their_accuracy_of_the_shop() -> None:
    reports = generate(scenario(location={"gps_rate": 1.0}), CATALOG).reports
    assert all(r.location is not None for r in reports)
    for r in reports:
        assert r.location is not None
        lat, lng, accuracy = r.location
        assert 10 <= accuracy <= 80
        assert haversine_m(GeoPoint(lat, lng), SHOPS[r.shop]) <= accuracy + 1
