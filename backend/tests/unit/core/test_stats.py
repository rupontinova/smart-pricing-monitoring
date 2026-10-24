import math

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from spm.constants import HALF_LIFE_MAX_H, HALF_LIFE_MIN_H, MAD_FLOOR_FRAC, MAD_SCALE
from spm.core.stats import (
    cap_group_share,
    daily_log_returns,
    effective_n,
    freshness_weight,
    half_life_from_volatility,
    log_ratio,
    mad,
    median,
    robust_z,
    weighted_mad,
    weighted_median,
    weighted_quantile,
)


# --------------------------------------------------------------------- weighted_quantile
def test_known_value_equal_weights_median() -> None:
    assert weighted_quantile([1, 2, 3, 4], [1, 1, 1, 1], 0.5) == 2.5


@pytest.mark.parametrize(
    ("values", "expected"),
    [([5.0], 5.0), ([3.0, 1.0, 2.0], 2.0), ([4.0, 1.0, 3.0, 2.0], 2.5), ([1.0, 1.0, 9.0], 1.0)],
)
def test_equal_weights_reproduce_interpolated_median(values: list[float], expected: float) -> None:
    assert median(values) == expected
    assert weighted_median(values, [3.0] * len(values)) == expected


@pytest.mark.parametrize("q", [0.0, 0.1, 0.5, 0.9, 1.0])
def test_single_value_for_every_q(q: float) -> None:
    assert weighted_quantile([42.0], [0.3], q) == 42.0


def test_quantile_ends_and_interpolation() -> None:
    values, weights = [10.0, 20.0, 30.0, 40.0], [1.0, 1.0, 1.0, 1.0]
    assert weighted_quantile(values, weights, 0.0) == 10.0  # below c_1 = 0.125
    assert weighted_quantile(values, weights, 0.125) == 10.0
    assert weighted_quantile(values, weights, 0.25) == pytest.approx(15.0)
    assert weighted_quantile(values, weights, 0.875) == 40.0
    assert weighted_quantile(values, weights, 1.0) == 40.0


def test_heavier_point_pulls_the_quantile() -> None:
    # Weights 3 and 1: positions 0.375 and 0.875 -> the median lies 1/4 of the way to 20.
    assert weighted_median([10.0, 20.0], [3.0, 1.0]) == pytest.approx(12.5)


def test_zero_weight_points_are_ignored() -> None:
    assert weighted_median([1.0, 2.0, 1000.0], [1.0, 1.0, 0.0]) == 1.5


@pytest.mark.parametrize(
    ("values", "weights", "q"),
    [
        ([], [], 0.5),
        ([1.0, 2.0], [0.0, 0.0], 0.5),
        ([1.0], [1.0, 2.0], 0.5),
        ([1.0], [-1.0], 0.5),
        ([1.0], [math.nan], 0.5),
        ([1.0], [1.0], 1.5),
        ([1.0], [1.0], -0.1),
    ],
)
def test_invalid_input_raises(values: list[float], weights: list[float], q: float) -> None:
    with pytest.raises(ValueError):
        weighted_quantile(values, weights, q)


finite = st.floats(min_value=-1e6, max_value=1e6, allow_nan=False)
positive_weight = st.floats(min_value=1e-3, max_value=1e3, allow_nan=False)
samples = st.lists(st.tuples(finite, positive_weight), min_size=1, max_size=30)
quantiles = st.floats(min_value=0.0, max_value=1.0)


@given(samples, quantiles, quantiles)
def test_monotone_in_q(points: list[tuple[float, float]], q1: float, q2: float) -> None:
    values, weights = zip(*points, strict=True)
    lo, hi = sorted((q1, q2))
    assert weighted_quantile(values, weights, lo) <= weighted_quantile(values, weights, hi) + 1e-9


@given(samples, quantiles)
def test_within_min_max(points: list[tuple[float, float]], q: float) -> None:
    values, weights = zip(*points, strict=True)
    result = weighted_quantile(values, weights, q)
    assert min(values) - 1e-9 <= result <= max(values) + 1e-9


@given(samples, quantiles, st.randoms(use_true_random=False))
def test_invariant_to_permutation(points: list[tuple[float, float]], q: float, rnd: object) -> None:
    shuffled = list(points)
    rnd.shuffle(shuffled)  # type: ignore[attr-defined]
    values, weights = zip(*points, strict=True)
    s_values, s_weights = zip(*shuffled, strict=True)
    a = weighted_quantile(values, weights, q)
    b = weighted_quantile(s_values, s_weights, q)
    assert a == pytest.approx(b, rel=1e-9, abs=1e-9)


@given(samples, quantiles, st.floats(min_value=1e-3, max_value=1e3))
def test_invariant_to_weight_scaling(points: list[tuple[float, float]], q: float, k: float) -> None:
    values, weights = zip(*points, strict=True)
    scaled = [w * k for w in weights]
    assert weighted_quantile(values, weights, q) == pytest.approx(
        weighted_quantile(values, scaled, q), rel=1e-6, abs=1e-6
    )


# Multiples of 1/4 shifted by integers stay exact in floating point, so shifting never merges
# two distinct values into a tie (which would legitimately change the quantile).
exact_values = st.integers(min_value=-4_000_000, max_value=4_000_000).map(lambda n: n / 4)


@given(
    st.lists(st.tuples(exact_values, positive_weight), min_size=1, max_size=30),
    quantiles,
    st.integers(min_value=-10_000, max_value=10_000),
)
def test_shift_equivariant(points: list[tuple[float, float]], q: float, c: int) -> None:
    values, weights = zip(*points, strict=True)
    shifted = [v + c for v in values]
    assert weighted_quantile(shifted, weights, q) == pytest.approx(
        weighted_quantile(values, weights, q) + c, rel=1e-9, abs=1e-6
    )


# ------------------------------------------------------------------------- MAD & robust z
def test_mad_known_values() -> None:
    assert mad([1.0, 2.0, 3.0, 4.0, 100.0]) == 1.0  # median 3; |dev| = 2,1,0,1,97
    assert mad([5.0, 5.0, 5.0]) == 0.0
    assert mad([1.0, 2.0, 3.0], center=0.0) == 2.0


def test_weighted_mad_uses_the_same_weights() -> None:
    values, weights = [10.0, 20.0, 30.0], [1.0, 1.0, 0.0]
    assert weighted_mad(values, weights) == 5.0  # center 15; |dev| 5, 5 (30 ignored)
    assert weighted_mad(values, weights, center=10.0) == 5.0


def test_robust_z_uses_floor_when_mad_is_zero() -> None:
    assert robust_z(85, 80, 0) == pytest.approx(5 / (MAD_SCALE * MAD_FLOOR_FRAC * 80))


def test_robust_z_uses_mad_above_floor() -> None:
    assert robust_z(90, 80, 4) == pytest.approx(10 / (MAD_SCALE * 4))
    assert robust_z(70, 80, 4) == pytest.approx(-10 / (MAD_SCALE * 4))


def test_robust_z_zero_denominator() -> None:
    assert robust_z(5, 0, 0) == 0.0


# ---------------------------------------------------------------------- weights
def test_freshness_weight() -> None:
    assert freshness_weight(48, 48) == 0.5
    assert freshness_weight(0, 72) == 1.0
    assert freshness_weight(96, 48) == 0.25
    assert freshness_weight(-5, 48) == 1.0  # future timestamps don't gain weight
    with pytest.raises(ValueError):
        freshness_weight(1, 0)


def test_effective_n() -> None:
    assert effective_n([1.0, 1.0, 1.0, 1.0]) == 4.0
    assert effective_n([1.0, 0.0, 0.0]) == 1.0
    assert effective_n([2.0, 1.0]) == pytest.approx(9 / 5)
    assert effective_n([]) == 0.0
    assert effective_n([0.0, 0.0]) == 0.0


# ----------------------------------------------------------------------- group cap
def _shares(weights: list[float], groups: list[str]) -> dict[str, float]:
    total = sum(weights)
    out: dict[str, float] = {}
    for w, g in zip(weights, groups, strict=True):
        out[g] = out.get(g, 0.0) + w / total
    return out


def test_one_shop_with_80_percent_is_capped_to_40() -> None:
    weights = [4.0, 4.0, 1.0, 1.0]  # shop-a holds 8 of 10
    groups = ["shop-a", "shop-a", "shop-b", "shop-c"]
    capped = cap_group_share(weights, groups, 0.40)
    shares = _shares(capped, groups)
    assert shares["shop-a"] == pytest.approx(0.40)
    assert capped[2:] == [1.0, 1.0]  # other shops untouched
    assert capped[0] == capped[1]  # proportional within the group


def test_group_pushed_over_by_capping_is_capped_too() -> None:
    # 45 / 45 / 10 with a 40% cap: one pass against the original totals would leave both ~44%.
    groups = ["a", "b", "c"]
    shares = _shares(cap_group_share([45.0, 45.0, 10.0], groups, 0.40), groups)
    assert shares["a"] == pytest.approx(0.40) and shares["b"] == pytest.approx(0.40)


def test_cap_impossible_makes_capped_groups_equal() -> None:
    # Two groups can't both be <= 40%: the best possible is 50/50.
    groups = ["a", "b"]
    shares = _shares(cap_group_share([9.0, 1.0], groups, 0.40), groups)
    assert shares == {"a": pytest.approx(0.5), "b": pytest.approx(0.5)}


def test_cap_no_op_cases() -> None:
    assert cap_group_share([1.0, 1.0, 1.0], ["a", "b", "c"], 0.40) == [1.0, 1.0, 1.0]
    assert cap_group_share([5.0, 1.0], ["a", "a"], 0.40) == [5.0, 1.0]  # one group: nothing to do
    assert cap_group_share([5.0, 1.0], ["a", "b"], 1.0) == [5.0, 1.0]
    assert cap_group_share([], [], 0.4) == []
    assert cap_group_share([0.0, 0.0], ["a", "b"], 0.4) == [0.0, 0.0]  # no weight: nothing to cap
    with pytest.raises(ValueError):
        cap_group_share([1.0], ["a", "b"], 0.4)
    with pytest.raises(ValueError):
        cap_group_share([1.0], ["a"], 0.0)


@given(
    st.lists(st.tuples(positive_weight, st.sampled_from("abcdefg")), min_size=1, max_size=40),
    st.floats(min_value=0.15, max_value=0.9),
)
def test_cap_property_no_group_exceeds_cap_when_feasible(
    points: list[tuple[float, str]], cap: float
) -> None:
    weights, groups = (list(x) for x in zip(*points, strict=True))
    assume(len(set(groups)) * cap >= 1.0 + 1e-9)  # feasible
    capped = cap_group_share(weights, groups, cap)
    assert max(_shares(capped, groups).values()) <= cap + 1e-9
    assert all(
        0 <= c <= w + 1e-12 for c, w in zip(capped, weights, strict=True)
    )  # only scaled down


# -------------------------------------------------------------- returns & half-life
def test_log_ratio() -> None:
    assert log_ratio(math.e, 1.0) == pytest.approx(1.0)
    assert log_ratio(80.0, 80.0) == 0.0
    with pytest.raises(ValueError):
        log_ratio(0.0, 1.0)
    with pytest.raises(ValueError):
        log_ratio(1.0, -1.0)


def test_daily_log_returns_skip_non_positive() -> None:
    assert daily_log_returns([100.0, 0.0, 110.0, -5.0, math.nan, 121.0]) == pytest.approx(
        [math.log(1.1), math.log(1.1)]
    )
    assert daily_log_returns([5.0]) == []


def _series_with_sigma(sigma: float, days: int = 31, start: float = 80.0) -> list[float]:
    """Alternating ±a log returns: median 0, MAD a, so MAD_SCALE × MAD = sigma exactly."""
    a = sigma / MAD_SCALE
    prices = [start]
    for i in range(days - 1):
        prices.append(prices[-1] * math.exp(a if i % 2 == 0 else -a))
    return prices


def test_half_life_constant_series_is_none() -> None:
    assert half_life_from_volatility([80.0] * 40) is None


def test_half_life_needs_ten_returns() -> None:
    assert half_life_from_volatility(_series_with_sigma(0.05, days=10)) is None  # 9 returns
    assert half_life_from_volatility(_series_with_sigma(0.05, days=11)) is not None


def test_half_life_sigma_005_is_48h() -> None:
    assert half_life_from_volatility(_series_with_sigma(0.05)) == 48


def test_half_life_sigma_0007_is_about_343h() -> None:
    assert half_life_from_volatility(_series_with_sigma(0.007)) == 343  # 0.1 / 0.007 × 24


def test_half_life_clamped() -> None:
    assert half_life_from_volatility(_series_with_sigma(0.5)) == HALF_LIFE_MIN_H  # 4.8 h -> 24
    assert half_life_from_volatility(_series_with_sigma(0.001)) == HALF_LIFE_MAX_H  # 2400 h -> 504
