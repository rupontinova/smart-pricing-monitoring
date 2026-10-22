"""Robust statistics (MODULES.md M09): weighted quantiles, MAD, robust z, freshness decay.

Pure functions on plain floats. Inputs are ``Sequence[float]``; the trust engine (M10), the
classifier (M11) and the band (M12) build on these.
"""

import itertools
import math
from collections import defaultdict
from collections.abc import Hashable, Sequence

from spm.constants import (
    HALF_LIFE_MAX_H,
    HALF_LIFE_MIN_H,
    HALF_LIFE_MIN_RETURNS,
    HALF_LIFE_VOLATILITY_K,
    MAD_FLOOR_FRAC,
    MAD_SCALE,
)


# ------------------------------------------------------------------------------ quantiles
def weighted_quantile(values: Sequence[float], weights: Sequence[float], q: float) -> float:
    """Weighted quantile with midpoint plotting positions.

    Points are sorted by value; with cumulative weight ``S_i`` and total ``S``, point ``i`` sits at
    ``c_i = (S_i − w_i / 2) / S``. Below ``c_1`` the result is the smallest value, above ``c_n`` the
    largest, and in between it interpolates linearly between the bracketing points. Zero-weight
    points are ignored. With equal weights this is the usual interpolated median/quantile.
    """
    if len(values) != len(weights):
        raise ValueError("values and weights must have the same length")
    if not 0.0 <= q <= 1.0:
        raise ValueError(f"q must be within [0, 1], got {q}")
    if any(w < 0 or not math.isfinite(w) for w in weights):
        raise ValueError("weights must be finite and non-negative")
    points = sorted((v, w) for v, w in zip(values, weights, strict=True) if w > 0)
    if not points:
        raise ValueError("no values with positive weight")
    total = math.fsum(w for _, w in points)

    cumulative = 0.0
    positions: list[float] = []
    for _, w in points:
        cumulative += w
        positions.append((cumulative - w / 2) / total)

    if q <= positions[0]:
        return points[0][0]
    if q >= positions[-1]:
        return points[-1][0]
    for i in range(1, len(points)):
        if q <= positions[i]:
            lo_c, hi_c = positions[i - 1], positions[i]
            lo_v, hi_v = points[i - 1][0], points[i][0]
            # c_i − c_{i−1} = (w_{i−1} + w_i) / 2S > 0, so this never divides by zero.
            return lo_v + (hi_v - lo_v) * (q - lo_c) / (hi_c - lo_c)
    raise AssertionError("unreachable: q < c_n was checked")  # pragma: no cover


def weighted_median(values: Sequence[float], weights: Sequence[float]) -> float:
    return weighted_quantile(values, weights, 0.5)


def median(values: Sequence[float]) -> float:
    return weighted_quantile(values, [1.0] * len(values), 0.5)


def mad(values: Sequence[float], center: float | None = None) -> float:
    """Median absolute deviation around ``center`` (default: the median)."""
    c = median(values) if center is None else center
    return median([abs(v - c) for v in values])


def weighted_mad(
    values: Sequence[float], weights: Sequence[float], center: float | None = None
) -> float:
    """Weighted median of absolute deviations around ``center`` (default: the weighted median)."""
    c = weighted_median(values, weights) if center is None else center
    return weighted_median([abs(v - c) for v in values], weights)


def robust_z(x: float, center: float, mad_value: float) -> float:
    """``(x − center) / (MAD_SCALE × max(MAD, MAD_FLOOR_FRAC × |center|))``; 0.0 if that is 0.

    The floor keeps identical prices (MAD = 0) from turning any small difference into a huge z.
    """
    scale = MAD_SCALE * max(mad_value, MAD_FLOOR_FRAC * abs(center))
    return 0.0 if scale == 0 else (x - center) / scale


# ------------------------------------------------------------------------------- weights
def freshness_weight(age_h: float, half_life_h: float) -> float:
    """``0.5 ** (age / half_life)``; future timestamps count as age 0."""
    if half_life_h <= 0:
        raise ValueError("half_life_h must be positive")
    return float(0.5 ** (max(age_h, 0.0) / half_life_h))


def effective_n(weights: Sequence[float]) -> float:
    """Kish effective sample size ``(Σw)² / Σw²`` (0 when there is no weight)."""
    sum_sq = math.fsum(w * w for w in weights)
    return 0.0 if sum_sq == 0 else math.fsum(weights) ** 2 / sum_sq


def cap_group_share[G: Hashable](
    weights: Sequence[float], groups: Sequence[G], max_share: float
) -> list[float]:
    """Scale down groups (e.g. shops) holding more than ``max_share`` of the total weight.

    Every over-share group is scaled so it ends at exactly ``max_share`` of the *new* total
    (water-filling: groups pushed over by others' capping are capped too). The others keep their
    weights; callers use relative weights, so nothing is renormalized. If capping is impossible
    (fewer than ``1 / max_share`` groups carry weight), the capped groups are made equal instead.
    """
    if len(weights) != len(groups):
        raise ValueError("weights and groups must have the same length")
    if not max_share > 0.0:
        raise ValueError("max_share must be positive")
    totals: dict[G, float] = defaultdict(float)
    for w, g in zip(weights, groups, strict=True):
        totals[g] += w
    if max_share >= 1.0 or len(totals) < 2:
        return list(weights)

    capped: set[G] = set()
    target: dict[G, float] = {}
    while True:
        free = math.fsum(t for g, t in totals.items() if g not in capped)
        room = 1.0 - len(capped) * max_share
        if room <= 0 or free == 0:  # can't cap: give the capped groups equal totals instead
            level = min(totals[g] for g in capped)
            target = {g: level for g in capped}
            break
        new_total = free / room  # total weight once every capped group sits at max_share
        target = {g: max_share * new_total for g in capped}
        over = {g for g, t in totals.items() if g not in capped and t > max_share * new_total}
        if not over:
            break
        capped |= over

    factor = {g: target[g] / totals[g] for g in capped if totals[g] > 0}
    return [w * factor.get(g, 1.0) for w, g in zip(weights, groups, strict=True)]


# --------------------------------------------------------------------- returns & volatility
def log_ratio(a: float, b: float) -> float:
    """``ln(a / b)`` for positive prices."""
    if a <= 0 or b <= 0:
        raise ValueError("log_ratio needs positive values")
    return math.log(a / b)


def daily_log_returns(series: Sequence[float]) -> list[float]:
    """Consecutive ``ln(p_t / p_{t−1})`` over the positive, finite values (others are skipped)."""
    prices = [p for p in series if p > 0 and math.isfinite(p)]
    return [math.log(b / a) for a, b in itertools.pairwise(prices)]


def half_life_from_volatility(daily_medians: Sequence[float]) -> int | None:
    """Half-life (hours) from day-to-day volatility: ``K / σ_daily`` days, clamped.

    ``σ_daily = MAD_SCALE × MAD(daily log returns)``. Volatile products get short half-lives
    (old prices go stale fast), stable ones long half-lives. None with fewer than
    ``HALF_LIFE_MIN_RETURNS`` returns or no volatility at all.
    """
    returns = daily_log_returns(daily_medians)
    if len(returns) < HALF_LIFE_MIN_RETURNS:
        return None
    sigma = MAD_SCALE * mad(returns)
    if sigma == 0:
        return None
    hours = HALF_LIFE_VOLATILITY_K / sigma * 24
    return round(min(max(hours, HALF_LIFE_MIN_H), HALF_LIFE_MAX_H))
