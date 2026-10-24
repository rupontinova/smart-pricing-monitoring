from dataclasses import dataclass, field
from decimal import Decimal

import pytest

from spm.constants import MAD_FLOOR_FRAC, MAD_SCALE, MARKET_SHIFT_WINDOW_H
from spm.core.classifier import (
    ClassificationContext,
    ShiftEvidence,
    build_shift_evidence,
    classify,
    market_window_h,
)
from spm.core.enums import Classification, Direction
from spm.core.models import Observation
from tests.fixtures.scenarios import (
    NOW,
    ONION,
    baseline,
    context,
    observation,
    shocked,
)

BASE = baseline()

C = Classification


@dataclass(frozen=True)
class Scenario:
    name: str
    new: Observation
    expected: Classification | set[Classification]
    direction: Direction | None = None
    history: list[Observation] = field(default_factory=lambda: BASE)
    extra: tuple[Observation, ...] = ()
    high_trust: bool = False
    insufficient: bool = False


SCENARIOS = [
    Scenario("normal", observation("shop-a", 81), C.NORMAL),
    Scenario("typo x1000", observation("shop-a", 0.08), C.BAD_REPORT, Direction.DOWN),
    Scenario("5x slip", observation("shop-a", 450), C.BAD_REPORT, Direction.UP),
    Scenario("single spike", observation("shop-c", 120), C.PENDING, Direction.UP),
    Scenario(
        "corroborated spike",
        observation("shop-c", 120),
        C.SHOP_OUTLIER,
        Direction.UP,
        extra=(observation("shop-c", 118, hours_ago=2, classification=C.PENDING),),
    ),
    Scenario(
        "high-trust spike",
        observation("shop-c", 120),
        C.SHOP_OUTLIER,
        Direction.UP,
        high_trust=True,
    ),
    Scenario(
        "deal",
        observation("shop-b", 60),
        C.SHOP_OUTLIER,
        Direction.DOWN,
        extra=(observation("shop-b", 61, hours_ago=1, classification=C.PENDING),),
    ),
    Scenario(
        "market shock",
        observation("shop-d", 112),
        C.MARKET_SHIFT,
        Direction.UP,
        history=shocked(BASE, ["shop-a", "shop-b", "shop-c"]),
    ),
    Scenario(
        "partial shock",
        observation("shop-c", 112),
        {C.PENDING, C.SHOP_OUTLIER},
        history=shocked(BASE, ["shop-a", "shop-b"]),
    ),
    Scenario("cold start", observation("shop-a", 81), C.NORMAL, history=[], insufficient=True),
    Scenario(
        "identical prices",
        observation("shop-a", 82),
        C.NORMAL,
        history=baseline(noise=0.0),
    ),
]


@pytest.mark.parametrize("s", SCENARIOS, ids=lambda s: s.name)
def test_scenarios(s: Scenario) -> None:
    result = classify(context(s.new, s.history, high_trust=s.high_trust, extra=s.extra))
    expected = s.expected if isinstance(s.expected, set) else {s.expected}
    assert result.classification in expected, result.reason
    if s.direction is not None:
        assert result.direction is s.direction
    assert result.insufficient_data is s.insufficient
    assert result.report_id == s.new.report_id
    assert result.reason


def test_relative_bounds_reference_is_90_day_median() -> None:
    result = classify(context(observation("shop-a", 450), BASE))
    assert result.reference_price == pytest.approx(80, abs=1)
    assert "relative bounds" in result.reason


def test_identical_prices_z_uses_mad_floor() -> None:
    result = classify(context(observation("shop-a", 82), baseline(noise=0.0)))
    floor = MAD_FLOOR_FRAC * 80  # every MAD is 0: the floor sets the scale
    assert result.z_market == pytest.approx(2 / (MAD_SCALE * floor))
    assert result.z_shop == pytest.approx(2 / (MAD_SCALE * floor))


def test_normal_fills_z_scores_and_reference() -> None:
    result = classify(context(observation("shop-a", 81), BASE))
    assert result.z_shop is not None and result.z_market is not None
    assert abs(result.z_market) < 3
    assert result.reference_price == pytest.approx(80, abs=1)
    assert result.direction is None


def test_pending_spike_reports_market_reference_and_z() -> None:
    result = classify(context(observation("shop-c", 120), BASE))
    assert result.z_market is not None and result.z_market > 3
    assert result.reference_price == pytest.approx(80, abs=1)


def test_market_shock_reference_is_median_of_baselines() -> None:
    result = classify(
        context(observation("shop-d", 112), shocked(BASE, ["shop-a", "shop-b", "shop-c"]))
    )
    assert result.reference_price == pytest.approx(80, abs=1)


def test_market_shift_seen_through_pending_reports() -> None:
    # In real scoring the shock's first reports are uncorroborated, hence PENDING.
    history = shocked(BASE, ["shop-a", "shop-b", "shop-c"], label=C.PENDING)
    result = classify(context(observation("shop-d", 112), history))
    assert result.classification is C.MARKET_SHIFT  # a, b, c moved, though all still PENDING


def test_market_shift_down() -> None:
    crash = shocked(BASE, ["shop-a", "shop-b", "shop-c"], price=55.0)
    result = classify(context(observation("shop-d", 54), crash))
    assert result.classification is C.MARKET_SHIFT and result.direction is Direction.DOWN


def test_shift_ignored_when_report_moves_the_other_way() -> None:
    # Prices rose at a, b, c, but this report is a cheap one: not a market shift.
    result = classify(
        context(observation("shop-d", 60), shocked(BASE, ["shop-a", "shop-b", "shop-c"]))
    )
    assert result.classification is not C.MARKET_SHIFT


def test_shift_needs_half_of_active_shops() -> None:
    history = shocked(BASE, ["shop-a", "shop-b", "shop-c"])
    result = classify(context(observation("shop-d", 112), history, active_shops=8))  # needs 4
    assert result.classification is not C.MARKET_SHIFT


def test_shop_jump_without_market_view_is_pending() -> None:
    history = [o for o in BASE if o.shop_id == "shop-a"]  # no other shops: no market
    result = classify(context(observation("shop-a", 120), history))
    assert result.classification is C.PENDING
    assert result.z_market is None and result.z_shop is not None and result.z_shop > 3
    assert "no market view" in result.reason


def test_relative_bounds_need_enough_area_history() -> None:
    few = BASE[:4]
    result = classify(context(observation("shop-a", 450), few))
    assert result.classification is not C.BAD_REPORT


def test_inputs_are_filtered_by_label_and_window() -> None:
    # Wrong labels or out-of-window observations passed in by mistake are ignored.
    noisy = [
        *BASE,
        *(observation("shop-b", 500, classification=C.BAD_REPORT) for _ in range(30)),
        *(observation("shop-b", 500, hours_ago=24 * 40) for _ in range(30)),
    ]
    assert classify(context(observation("shop-a", 81), noisy)).classification is C.NORMAL


def test_zero_trust_market_falls_back_to_unweighted() -> None:
    history = [
        observation(
            o.shop_id,
            o.norm_price,
            hours_ago=(NOW - o.observed_at).total_seconds() / 3600,
            trust=0.0,
        )
        for o in BASE
    ]
    assert classify(context(observation("shop-a", 81), history)).classification is C.NORMAL


# --------------------------------------------------------------------------- helpers
def test_market_window() -> None:
    assert market_window_h(48) == 96
    assert market_window_h(12) == 72  # 3-day floor
    assert market_window_h(504) == 1008


def test_build_shift_evidence() -> None:
    history = shocked(BASE, ["shop-a"])
    by_shop = {"shop-a": [o for o in history if o.shop_id == "shop-a"], "empty": []}
    evidence = build_shift_evidence(by_shop, NOW)
    a = evidence["shop-a"]
    assert a.recent_median == pytest.approx(110, abs=2)
    assert a.baseline_median == pytest.approx(80, abs=2)
    assert a.baseline_mad is not None and a.baseline_mad < 2
    assert len(a.recent_prices) == 2 * MARKET_SHIFT_WINDOW_H // 24  # 2 per day in the window
    assert evidence["empty"] == ShiftEvidence("empty", None, None, None, ())


def test_shift_without_own_baseline_uses_market_center() -> None:
    # The report's shop is new (no baseline): direction comes from the market center.
    history = shocked(BASE, ["shop-a", "shop-b", "shop-c"])
    ctx = context(observation("shop-z", 112), history)
    assert classify(ctx).classification is C.MARKET_SHIFT


def test_context_dataclass_defaults() -> None:
    ctx = ClassificationContext(
        now=NOW,
        product=ONION,
        report=observation("shop-a", 80),
        reporter_high_trust=False,
        area_median_90d=None,
        area_n_90d=0,
        shop_history=[],
        market=[],
        same_shop_recent=[],
    )
    result = classify(ctx)
    assert result.classification is C.NORMAL and result.insufficient_data
    assert ONION.abs_min == Decimal(10)
