"""Classifier scenarios (MODULES.md M11): a stable onion market and builders for the new report.

Baseline: 5 shops, onion at ~80 ± 2 over 30 days, 2 reports per shop per day (6 h and 18 h after
midnight-ish relative to NOW), honest independent reporters with trust 0.6.
"""

import random
import uuid
from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from spm.core.classifier import ClassificationContext, build_shift_evidence, market_window_h
from spm.core.enums import BaseUnit, Classification
from spm.core.models import Observation, Product
from spm.core.stats import median
from spm.core.trust import corroboration_window_h

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
SHOPS = ("shop-a", "shop-b", "shop-c", "shop-d", "shop-e")
ONION = Product("onion", "Onion", BaseUnit.KG, "vegetable", 48, Decimal(10), Decimal(500))
HONEST_TRUST = 0.6


def observation(
    shop: str,
    price: float,
    *,
    hours_ago: float = 0.0,
    classification: Classification | None = Classification.NORMAL,
    trust: float = HONEST_TRUST,
    reporter: uuid.UUID | None = None,
) -> Observation:
    return Observation(
        report_id=uuid.uuid4(),
        reporter_id=reporter or uuid.uuid4(),
        shop_id=shop,
        norm_price=price,
        observed_at=NOW - timedelta(hours=hours_ago),
        trust=trust,
        location_verified=True,
        classification=classification,
        ip_hash=None,
    )


def baseline(
    *, days: int = 30, price: float = 80.0, noise: float = 2.0, seed: int = 7
) -> list[Observation]:
    rnd = random.Random(seed)
    return [
        observation(shop, round(price + rnd.uniform(-noise, noise), 2), hours_ago=day * 24 + offset)
        for shop in SHOPS
        for day in range(days)
        for offset in (6, 18)
    ]


def shocked(
    history: Iterable[Observation],
    shops: Sequence[str],
    price: float = 110.0,
    label: Classification = Classification.NORMAL,
) -> list[Observation]:
    """The given shops' last-48 h reports moved to ~``price`` (a supply shock).

    ``label`` is how scoring would have classified them (PENDING when uncorroborated).
    """
    rnd = random.Random(11)
    out = []
    for o in history:
        if o.shop_id in shops and (NOW - o.observed_at) <= timedelta(hours=48):
            o = observation(
                o.shop_id,
                round(price + rnd.uniform(-2, 2), 2),
                hours_ago=(NOW - o.observed_at).total_seconds() / 3600,
                classification=label,
            )
        out.append(o)
    return out


def context(
    new: Observation,
    history: Sequence[Observation],
    *,
    high_trust: bool = False,
    extra: Sequence[Observation] = (),
    active_shops: int = len(SHOPS),
) -> ClassificationContext:
    """Build the context M13 would fetch, from plain observation lists."""
    everything = [*history, *extra]

    def age_h(o: Observation) -> float:
        return (NOW - o.observed_at).total_seconds() / 3600

    area = [
        o.norm_price
        for o in everything
        if o.classification in (Classification.NORMAL, Classification.MARKET_SHIFT)
        and age_h(o) <= 90 * 24
    ]
    by_shop: dict[str, list[Observation]] = defaultdict(list)
    for o in everything:
        by_shop[o.shop_id].append(o)
    return ClassificationContext(
        now=NOW,
        product=ONION,
        report=new,
        reporter_high_trust=high_trust,
        area_median_90d=median(area) if area else None,
        area_n_90d=len(area),
        shop_history=[o for o in everything if o.shop_id == new.shop_id and age_h(o) <= 30 * 24],
        market=[
            o
            for o in everything
            if o.shop_id != new.shop_id and age_h(o) <= market_window_h(ONION.half_life_h)
        ],
        same_shop_recent=[
            o
            for o in everything
            if o.shop_id == new.shop_id and age_h(o) <= corroboration_window_h(ONION.half_life_h)
        ],
        shift_candidates=build_shift_evidence(by_shop, NOW),
        active_shops_in_area=active_shops,
    )
