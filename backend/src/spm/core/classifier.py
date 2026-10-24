"""Anomaly classifier (badge E): who is wrong, the report, the shop or the market?

``classify`` is a pure function over a prepared ``ClassificationContext`` (fetched by M13). The
steps run exactly in the order of MODULES.md M11; the first decisive one wins.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from spm.constants import (
    BOUNDS_HIGH_MULT,
    BOUNDS_LOW_MULT,
    BOUNDS_MIN_N,
    MARKET_MIN_N,
    MARKET_SHIFT_MIN_SHOPS,
    MARKET_SHIFT_WINDOW_H,
    MARKET_WINDOW_MIN_D,
    SHOP_HISTORY_D,
    SHOP_HISTORY_MIN_N,
    Z_THRESHOLD,
)
from spm.core.enums import Classification, Direction
from spm.core.models import Assessment, Observation, Product
from spm.core.stats import mad, median, robust_z, weighted_mad, weighted_median
from spm.core.trust import corroboration_window_h, is_corroborated, report_weight

# Which labels count as evidence (MODULES.md §3.3).
SHOP_INCLUDED = {Classification.NORMAL, Classification.SHOP_OUTLIER, Classification.MARKET_SHIFT}
MARKET_INCLUDED = {Classification.NORMAL, Classification.MARKET_SHIFT}  # band: no SHOP_OUTLIER
CORROBORATION_EXCLUDED = {Classification.BAD_REPORT, Classification.EXPIRED}
# A market shock first shows up as uncorroborated (PENDING) jumps at several shops, so the
# recent side of the shift evidence counts them; the baseline stays clean.
SHIFT_RECENT_INCLUDED = SHOP_INCLUDED | {Classification.PENDING}


@dataclass(frozen=True, slots=True)
class ShiftEvidence:
    """One shop's recent (48 h) prices against its own 30-day baseline (before those 48 h)."""

    shop_id: str
    recent_median: float | None
    baseline_median: float | None
    baseline_mad: float | None
    recent_prices: tuple[float, ...] = ()  # so the new report can be added to its shop's set


@dataclass(frozen=True, slots=True)
class ClassificationContext:
    now: datetime
    product: Product
    report: Observation  # the new report (trust filled in)
    reporter_high_trust: bool
    area_median_90d: float | None  # unweighted, included classifications only
    area_n_90d: int
    shop_history: Sequence[
        Observation
    ]  # same shop & product, SHOP_HISTORY_D, excluding this report
    market: Sequence[Observation]  # other shops, same area & product, market window
    same_shop_recent: Sequence[Observation]  # same shop & product within the corroboration window
    shift_candidates: Mapping[str, ShiftEvidence] = field(default_factory=dict)
    active_shops_in_area: int = 0


def market_window_h(half_life_h: int) -> float:
    """``max(MARKET_WINDOW_MIN_D days, 2 × half-life)`` in hours."""
    return max(MARKET_WINDOW_MIN_D * 24.0, 2.0 * half_life_h)


def _age_h(now: datetime, o: Observation) -> float:
    return (now - o.observed_at).total_seconds() / 3600


def build_shift_evidence(
    obs_by_shop: Mapping[str, Sequence[Observation]],
    now: datetime,
    *,
    recent_h: float = MARKET_SHIFT_WINDOW_H,
    baseline_d: float = SHOP_HISTORY_D,
) -> dict[str, ShiftEvidence]:
    """Per shop: median of the last ``recent_h`` vs median/MAD of the ``baseline_d`` days before.

    The baseline counts included labels only (as for shop history); the recent window also
    counts PENDING, because that is how a shock first appears. Unweighted: a shift is about where
    prices sit now, not about who reported them.
    """
    evidence: dict[str, ShiftEvidence] = {}
    for shop_id, observations in obs_by_shop.items():
        recent = [
            o.norm_price
            for o in observations
            if o.classification in SHIFT_RECENT_INCLUDED and _age_h(now, o) <= recent_h
        ]
        baseline = [
            o.norm_price
            for o in observations
            if o.classification in SHOP_INCLUDED
            and recent_h < _age_h(now, o) <= baseline_d * 24 + recent_h
        ]
        evidence[shop_id] = ShiftEvidence(
            shop_id=shop_id,
            recent_median=median(recent) if recent else None,
            baseline_median=median(baseline) if baseline else None,
            baseline_mad=mad(baseline) if baseline else None,
            recent_prices=tuple(recent),
        )
    return evidence


def _weighted_center(
    obs: Sequence[Observation], now: datetime, half_life_h: int
) -> tuple[float, float]:
    prices = [o.norm_price for o in obs]
    weights = [
        report_weight(o.trust, _age_h(now, o), half_life_h, o.location_verified) for o in obs
    ]
    if sum(weights) == 0:  # e.g. every reporter at trust 0: fall back to unweighted
        return median(prices), mad(prices)
    center = weighted_median(prices, weights)
    return center, weighted_mad(prices, weights, center)


def _direction(value: float) -> Direction:
    return Direction.UP if value > 0 else Direction.DOWN


def _fmt(x: float) -> str:
    return f"{x:.2f}"


def classify(ctx: ClassificationContext) -> Assessment:
    r = ctx.report
    p = r.norm_price
    half_life = ctx.product.half_life_h

    def assessment(
        label: Classification,
        reason: str,
        *,
        direction: Direction | None = None,
        reference: float | None = None,
        z_shop: float | None = None,
        z_market: float | None = None,
        insufficient_data: bool = False,
    ) -> Assessment:
        return Assessment(
            report_id=r.report_id,
            classification=label,
            direction=direction,
            z_shop=z_shop,
            z_market=z_market,
            reference_price=reference,
            insufficient_data=insufficient_data,
            reason=reason,
        )

    # 1. Weights are applied inside the weighted centers below (trust × freshness × location).

    # 2. Relative bounds against the 90-day area median.
    m90 = ctx.area_median_90d
    if (
        m90 is not None
        and ctx.area_n_90d >= BOUNDS_MIN_N
        and (p < BOUNDS_LOW_MULT * m90 or p > BOUNDS_HIGH_MULT * m90)
    ):
        return assessment(
            Classification.BAD_REPORT,
            f"outside relative bounds: {_fmt(p)} vs 90-day area median {_fmt(m90)} "
            f"(allowed {BOUNDS_LOW_MULT}x to {BOUNDS_HIGH_MULT}x)",
            direction=_direction(p - m90),
            reference=m90,
        )

    # 3. z against this shop's own history.
    history = [
        o for o in ctx.shop_history
        if o.report_id != r.report_id and o.classification in SHOP_INCLUDED
        and _age_h(ctx.now, o) <= SHOP_HISTORY_D * 24
    ]  # fmt: skip
    z_shop: float | None = None
    shop_center: float | None = None
    if len(history) >= SHOP_HISTORY_MIN_N:
        shop_center, shop_mad = _weighted_center(history, ctx.now, half_life)
        z_shop = robust_z(p, shop_center, shop_mad)

    # 4. z against the rest of the market (other shops, market window).
    window_h = market_window_h(half_life)
    market = [
        o for o in ctx.market
        if o.report_id != r.report_id and o.shop_id != r.shop_id
        and o.classification in MARKET_INCLUDED and _age_h(ctx.now, o) <= window_h
    ]  # fmt: skip
    z_market: float | None = None
    market_center: float | None = None
    if len(market) >= MARKET_MIN_N:
        market_center, market_mad = _weighted_center(market, ctx.now, half_life)
        z_market = robust_z(p, market_center, market_mad)

    # 5. Market shift: enough shops moved, in the same direction as this report.
    shifted = _market_shift(ctx, market_center)
    if shifted is not None:
        direction, count, baselines = shifted
        return assessment(
            Classification.MARKET_SHIFT,
            f"market shift {direction.value}: {count} of {ctx.active_shops_in_area} shops "
            f"moved beyond z {Z_THRESHOLD} from their own baselines "
            f"in the last {MARKET_SHIFT_WINDOW_H} h",
            direction=direction,
            reference=median(baselines),
            z_shop=z_shop,
            z_market=z_market,
        )

    # 6. Out of line with the market: a shop outlier if corroborated, else pending.
    if z_market is not None and market_center is not None and abs(z_market) > Z_THRESHOLD:
        direction = _direction(z_market)
        recent = [o for o in ctx.same_shop_recent if o.classification not in CORROBORATION_EXCLUDED]
        ok, support = is_corroborated(
            r, recent, corroboration_window_h(half_life), ctx.reporter_high_trust
        )
        if ok:
            how = (
                f"{len(support)} independent agreeing report(s)"
                if support
                else "high-trust reporter"
            )
            return assessment(
                Classification.SHOP_OUTLIER,
                f"shop outlier ({direction.value}): z_market {z_market:.1f} vs market "
                f"{_fmt(market_center)}, corroborated by {how}",
                direction=direction,
                reference=market_center,
                z_shop=z_shop,
                z_market=z_market,
            )
        return assessment(
            Classification.PENDING,
            f"pending: z_market {z_market:.1f} vs market {_fmt(market_center)}, "
            "awaiting corroboration",
            direction=direction,
            reference=market_center,
            z_shop=z_shop,
            z_market=z_market,
        )

    # 7. A jump at this shop with no market view: wait for corroboration or contradiction.
    if (
        z_market is None
        and z_shop is not None
        and shop_center is not None
        and abs(z_shop) > Z_THRESHOLD
    ):
        direction = _direction(z_shop)
        return assessment(
            Classification.PENDING,
            f"pending: z_shop {z_shop:.1f} vs shop history {_fmt(shop_center)}, no market view",
            direction=direction,
            reference=shop_center,
            z_shop=z_shop,
            z_market=z_market,
        )

    # 8. Normal.
    reference = market_center if market_center is not None else shop_center
    if z_shop is None and z_market is None:
        reason = "normal: insufficient data (no shop history or market yet)"
    else:
        named = (("z_shop", z_shop), ("z_market", z_market))
        parts = [f"{k} {v:.1f}" for k, v in named if v is not None]
        reason = f"normal: {', '.join(parts)} within {Z_THRESHOLD}"
    return assessment(
        Classification.NORMAL,
        reason,
        reference=reference,
        z_shop=z_shop,
        z_market=z_market,
        insufficient_data=z_shop is None and z_market is None,
    )


def _market_shift(
    ctx: ClassificationContext, market_center: float | None
) -> tuple[Direction, int, list[float]] | None:
    """(direction, shops moved, their baselines) if this looks like a market-wide move."""
    r = ctx.report
    candidates = dict(ctx.shift_candidates)
    own = candidates.get(r.shop_id, ShiftEvidence(r.shop_id, None, None, None))
    own_recent = (*own.recent_prices, r.norm_price)
    candidates[r.shop_id] = ShiftEvidence(
        r.shop_id, median(own_recent), own.baseline_median, own.baseline_mad, own_recent
    )

    # Direction of this report relative to its own shop's baseline (else the market).
    reference = own.baseline_median if own.baseline_median is not None else market_center
    if reference is None or r.norm_price == reference:
        return None
    sign = 1 if r.norm_price > reference else -1

    moved: list[float] = []
    for ev in candidates.values():
        if ev.recent_median is None or ev.baseline_median is None:
            continue
        z = robust_z(ev.recent_median, ev.baseline_median, ev.baseline_mad or 0.0)
        if abs(z) > Z_THRESHOLD and (z > 0) == (sign > 0):
            moved.append(ev.baseline_median)
    needed = max(MARKET_SHIFT_MIN_SHOPS, math.ceil(0.5 * ctx.active_shops_in_area))
    if len(moved) >= needed:
        return _direction(sign), len(moved), moved
    return None
