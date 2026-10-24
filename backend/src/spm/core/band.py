"""Fair-price band and shop prices (badges F and G): freshness- and trust-weighted, per
product × area. Pure; ``BandService`` loads the observations and persists/caches the results.
"""

import math
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta
from uuid import UUID

from spm.constants import (
    BAND_MAX_SHOP_SHARE,
    BAND_WINDOW_HALF_LIVES,
    BAND_WINDOW_MAX_D,
    CACHE_TTL_MIN_H,
    CONF_HIGH_MIN_SHOPS,
    CONF_HIGH_WEIGHT,
    CONF_LOW,
    CONF_MEDIUM,
    SHOP_PRICE_MIN_FRESHNESS,
)
from spm.core.enums import Classification, Confidence
from spm.core.models import FairBand, Observation, ShopPrice
from spm.core.stats import (
    cap_group_share,
    effective_n,
    freshness_weight,
    mad,
    weighted_mad,
    weighted_quantile,
)
from spm.core.trust import report_weight, verified_shop_price

BAND_INCLUDED = {Classification.NORMAL, Classification.MARKET_SHIFT}
SHOP_INCLUDED = {Classification.NORMAL, Classification.MARKET_SHIFT, Classification.SHOP_OUTLIER}


def _age_h(now: datetime, o: Observation) -> float:
    return max((now - o.observed_at).total_seconds() / 3600, 0.0)


def band_window_start(now: datetime, half_life_h: int, rebase_at: datetime | None) -> datetime:
    """``now − min(4 half-lives, 60 days)``, or ``rebase_at`` if that is later."""
    span = min(
        timedelta(hours=BAND_WINDOW_HALF_LIVES * half_life_h), timedelta(days=BAND_WINDOW_MAX_D)
    )
    start = now - span
    return max(start, rebase_at) if rebase_at is not None else start


def _confidence(total_weight: float, n_shops: int) -> Confidence:
    if total_weight >= CONF_HIGH_WEIGHT and n_shops >= CONF_HIGH_MIN_SHOPS:
        return Confidence.HIGH
    if total_weight >= CONF_MEDIUM:
        return Confidence.MEDIUM
    if total_weight >= CONF_LOW:
        return Confidence.LOW
    return Confidence.STALE


def compute_band(
    product_id: str,
    area_id: str,
    obs: Sequence[Observation],
    now: datetime,
    half_life_h: int,
    rebase_at: datetime | None,
) -> FairBand:
    """The area's fair band from NORMAL / MARKET_SHIFT reports inside the band window.

    Weights are trust × freshness × location; no single shop may carry more than
    ``BAND_MAX_SHOP_SHARE`` of the weight. Confidence comes from the uncapped total weight.
    """
    start = band_window_start(now, half_life_h, rebase_at)
    used = [o for o in obs if o.classification in BAND_INCLUDED and o.observed_at >= start]
    if not used:
        return FairBand(
            product_id, area_id, None, None, None, None, 0.0, 0.0, 0, Confidence.STALE,
            None, None, half_life_h, rebase_at, now,
        )  # fmt: skip

    prices = [o.norm_price for o in used]
    ages = [_age_h(now, o) for o in used]
    weights = [
        report_weight(o.trust, age, half_life_h, o.location_verified)
        for o, age in zip(used, ages, strict=True)
    ]
    total = math.fsum(weights)
    capped = cap_group_share(weights, [o.shop_id for o in used], BAND_MAX_SHOP_SHARE)

    if total > 0:
        p25, p50, p75 = (weighted_quantile(prices, capped, q) for q in (0.25, 0.5, 0.75))
        spread = weighted_mad(prices, capped, p50)
        mean_age = math.fsum(w * a for w, a in zip(weights, ages, strict=True)) / total
    else:  # all reporters at trust 0: describe the data unweighted (confidence stays STALE)
        ones = [1.0] * len(prices)
        p25, p50, p75 = (weighted_quantile(prices, ones, q) for q in (0.25, 0.5, 0.75))
        spread = mad(prices, p50)
        mean_age = math.fsum(ages) / len(ages)

    n_shops = len({o.shop_id for o, w in zip(used, weights, strict=True) if w > 0})
    return FairBand(
        product_id=product_id,
        area_id=area_id,
        p25=p25,
        p50=p50,
        p75=p75,
        mad=spread,
        total_weight=total,
        n_eff=effective_n(capped),
        n_shops=n_shops,
        confidence=_confidence(total, n_shops),
        newest_obs_at=max(o.observed_at for o in used),
        mean_age_h=mean_age,
        half_life_h=half_life_h,
        rebase_at=rebase_at,
        computed_at=now,
    )


def compute_shop_prices(
    product_id: str,
    area_id: str,
    obs_by_shop: Mapping[str, Sequence[Observation]],
    now: datetime,
    half_life_h: int,
    high_trust: Callable[[UUID], bool],
) -> list[ShopPrice]:
    """Each shop's current price; shops whose newest report is too old are left out.

    VERIFIED prices come from ``trust.verified_shop_price``. Otherwise the shop's most recent
    included price is shown, marked unverified.
    """
    result: list[ShopPrice] = []
    for shop_id in sorted(obs_by_shop):
        included = [o for o in obs_by_shop[shop_id] if o.classification in SHOP_INCLUDED]
        if not included:
            continue
        newest = max(included, key=lambda o: (o.observed_at, o.report_id))
        if freshness_weight(_age_h(now, newest), half_life_h) < SHOP_PRICE_MIN_FRESHNESS:
            continue
        verified, price, support_ids = verified_shop_price(included, now, half_life_h, high_trust)
        support = [o for o in included if o.report_id in set(support_ids)]
        if not verified or price is None:
            verified, price, support = False, newest.norm_price, [newest]
        result.append(
            ShopPrice(
                product_id=product_id,
                shop_id=shop_id,
                area_id=area_id,
                price=price,
                verified=verified,
                support_weight=math.fsum(
                    report_weight(o.trust, _age_h(now, o), half_life_h, o.location_verified)
                    for o in support
                ),
                n_reports=len(support),
                last_observed_at=newest.observed_at,
                # When the evidence for the verified price was complete: its latest report.
                verified_at=max(o.observed_at for o in support) if verified else None,
            )
        )
    return result


def cache_ttl_s(half_life_h: int) -> int:
    """Hot-cache TTL: ``max(2 × half-life, 24 h)`` in seconds."""
    return int(max(2 * half_life_h, CACHE_TTL_MIN_H) * 3600)
