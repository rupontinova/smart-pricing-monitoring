"""Synthetic markets with known anomalies: a time-ordered stream of ``SimReport`` with ground truth.

The true price of a product at a shop at time t is

    market(t) × shock(t) × (1 + shop_premium) × gouging(t)

where ``market`` is a geometric random walk (one step per day, interpolated in log space), ``shock``
ramps linearly to its multiplier and stays there, and ``gouging`` is a step for one shop. The *fair*
area price (what the band's p50 should track) is the median over shops without gouging.

Everything is drawn from ``random.Random`` streams seeded by the scenario, so a seed always yields
the same reports. Market and reporters use separate streams: changing the crowd keeps the market.
"""

import math
import random
import statistics
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from enum import StrEnum
from zoneinfo import ZoneInfo

from simulator.scenario import MarketShock, Scenario, ShopGouging
from spm.adapters.catalog_yaml import SeedCatalog
from spm.constants import AGREE_LOG_TOL, MARKET_SHIFT_WINDOW_H
from spm.core.enums import AnomalyKind, BaseUnit, Direction

SHOPPING_HOURS = (time(8), time(22))  # area-local
SUBMIT_DELAY_MIN = (1, 20)  # minutes between observing a price and submitting it
METERS_PER_DEG_LAT = 111_320

_CENT = Decimal("0.01")


class Truth(StrEnum):
    NORMAL = "normal"
    BAD_UNIT = "bad_unit"
    MALICIOUS = "malicious"
    SHOP_OUTLIER = "shop_outlier"
    MARKET_SHIFT = "market_shift"


@dataclass(frozen=True, slots=True)
class SimReport:
    seq: int
    device_id: str
    reporter_kind: str  # honest | sloppy | malicious | spammer
    ip_hash: str
    client_report_id: uuid.UUID
    product: str
    shop: str
    price: Decimal  # what the reporter typed
    quantity: Decimal
    unit: str
    observed_at: datetime
    submitted_at: datetime
    location: tuple[float, float, float] | None  # lat, lng, accuracy_m
    true_price: float  # per base unit at this shop, at observed_at
    truth: Truth
    error_mode: str | None = None


@dataclass(frozen=True, slots=True)
class EventWindow:
    """A true event, and the anomaly that should report it."""

    index: int
    label: str
    expected_kind: AnomalyKind
    product: str
    shop: str | None
    direction: Direction
    start: datetime
    end: datetime  # when the deviation is fully in place (shock) or over (gouging)


class Market:
    def __init__(self, scenario: Scenario, tz: ZoneInfo) -> None:
        self.scenario = scenario
        self.tz = tz
        self.origin = self.local(scenario.start, time(0))  # day 0, local midnight
        rng = random.Random(f"{scenario.seed}:market")
        self._log_walk: dict[str, list[float]] = {}
        for product in scenario.products:
            sigma = scenario.daily_volatility.get(product, 0.0)
            walk = [math.log(scenario.true_price[product])]
            for _ in range(scenario.days + 1):
                walk.append(walk[-1] + rng.gauss(0.0, sigma))
            self._log_walk[product] = walk

    def local(self, day: date, at: time) -> datetime:
        return datetime.combine(day, at, self.tz).astimezone(UTC)

    def event_start(self, event: MarketShock | ShopGouging) -> datetime:
        return self.local(self.scenario.start + timedelta(days=event.day), event.time)

    def _day(self, t: datetime) -> float:
        return (t - self.origin).total_seconds() / 86_400

    def base(self, product: str, t: datetime) -> float:
        walk = self._log_walk[product]
        d = min(max(self._day(t), 0.0), len(walk) - 1.0)
        i = min(int(d), len(walk) - 2)
        return math.exp(walk[i] + (walk[i + 1] - walk[i]) * (d - i))

    def shock(self, product: str, t: datetime) -> float:
        factor = 1.0
        for event in self.scenario.events:
            if isinstance(event, MarketShock) and event.product == product:
                elapsed_h = (t - self.event_start(event)).total_seconds() / 3600
                if elapsed_h <= 0:
                    continue
                ramp = 1.0 if event.ramp_hours == 0 else min(elapsed_h / event.ramp_hours, 1.0)
                factor *= 1 + (event.multiplier - 1) * ramp
        return factor

    def gouging(self, product: str, shop: str, t: datetime) -> float:
        factor = 1.0
        for event in self.scenario.events:
            if (
                isinstance(event, ShopGouging)
                and event.product == product
                and event.shop == shop
                and 0
                <= (t - self.event_start(event)).total_seconds() / 86_400
                < event.duration_days
            ):
                factor *= event.multiplier
        return factor

    def shop_price(self, product: str, shop: str, t: datetime) -> float:
        premium = 1 + self.scenario.shop_premium[shop]
        return (
            self.base(product, t)
            * self.shock(product, t)
            * premium
            * self.gouging(product, shop, t)
        )

    def fair_price(self, product: str, t: datetime) -> float:
        """The fair area price: median over shops, without gouging."""
        level = self.base(product, t) * self.shock(product, t)
        return statistics.median(level * (1 + p) for p in self.scenario.shop_premium.values())

    def in_shift(self, product: str, t: datetime) -> bool:
        """A report reflects a market shift while the shock is visible and still recent."""
        for event in self.scenario.events:
            if isinstance(event, MarketShock) and event.product == product:
                start = self.event_start(event)
                end = start + timedelta(hours=event.ramp_hours + MARKET_SHIFT_WINDOW_H)
                if start <= t < end and abs(math.log(self.shock(product, t))) >= AGREE_LOG_TOL:
                    return True
        return False


def event_windows(scenario: Scenario, market: Market) -> list[EventWindow]:
    windows = []
    for i, event in enumerate(scenario.events):
        start = market.event_start(event)
        direction = Direction.UP if event.multiplier >= 1 else Direction.DOWN
        if isinstance(event, MarketShock):
            windows.append(
                EventWindow(
                    i,
                    f"market_shock {event.product} ×{event.multiplier:g} "
                    f"(day {event.day}, {event.time:%H:%M})",
                    AnomalyKind.MARKET_SHIFT,
                    event.product,
                    None,
                    direction,
                    start,
                    start + timedelta(hours=event.ramp_hours),
                )
            )
        else:
            windows.append(
                EventWindow(
                    i,
                    f"shop_gouging {event.product}@{event.shop} ×{event.multiplier:g} "
                    f"(day {event.day}, {event.time:%H:%M}, {event.duration_days:g} d)",
                    AnomalyKind.SHOP_OUTLIER,
                    event.product,
                    event.shop,
                    direction,
                    start,
                    start + timedelta(days=event.duration_days),
                )
            )
    return windows


@dataclass(frozen=True, slots=True)
class SimStream:
    scenario: Scenario
    reports: list[SimReport]
    events: list[EventWindow]
    market: Market
    half_lives: dict[str, int]  # per product, from the catalog (corroboration windows)

    @property
    def start(self) -> datetime:
        return self.market.origin

    @property
    def end(self) -> datetime:
        return self.market.origin + timedelta(days=self.scenario.days)


# ------------------------------------------------------------------------------- generation
def _poisson(rng: random.Random, lam: float) -> int:
    """Knuth's method; λ here is small (reports per reporter per day)."""
    if lam <= 0:
        return 0
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def _money(x: float) -> Decimal:
    return Decimal(repr(x)).quantize(_CENT, rounding=ROUND_HALF_EVEN)


# (quantity, unit, size in base units, probability) of what honest shoppers buy.
_BASKETS: dict[BaseUnit, list[tuple[str, str, float, float]]] = {
    BaseUnit.KG: [("1", "kg", 1.0, 0.5), ("500", "g", 0.5, 0.3), ("2", "kg", 2.0, 0.2)],
    BaseUnit.L: [("1", "l", 1.0, 0.6), ("500", "ml", 0.5, 0.2), ("2", "l", 2.0, 0.2)],
    BaseUnit.DOZEN: [("1", "dozen", 1.0, 0.5), ("1", "hali", 1 / 3, 0.3), ("1", "tray", 2.5, 0.2)],
}


class _Builder:
    def __init__(self, scenario: Scenario, catalog: SeedCatalog, market: Market) -> None:
        self.s = scenario
        self.market = market
        self.rng = random.Random(f"{scenario.seed}:crowd")
        self.base_unit = {p.id: p.base_unit for p in catalog.products}
        self.shop_location = {s.id: (s.location.lat, s.location.lng) for s in catalog.shops}
        self.tz = market.tz
        for product in scenario.products:
            if product not in self.base_unit:
                raise ValueError(f"product {product!r} is not in the catalog")
        for shop in scenario.shops:
            if shop not in self.shop_location:
                raise ValueError(f"shop {shop!r} is not in the catalog")
        self.reports: list[SimReport] = []
        weights = scenario.product_weights
        self.product_weights = [weights.get(p, 1.0) for p in scenario.products]

    # ---- helpers
    def shopping_time(self, day: int) -> datetime:
        local_date = self.s.start + timedelta(days=day)
        open_, close = (datetime.combine(local_date, t, self.tz) for t in SHOPPING_HOURS)
        return (open_ + (close - open_) * self.rng.random()).astimezone(UTC)

    def location(self, shop: str, gps_rate: float) -> tuple[float, float, float] | None:
        if self.rng.random() >= gps_rate:
            return None
        lo, hi = self.s.location.accuracy_m
        accuracy = self.rng.uniform(lo, hi)
        distance = min(abs(self.rng.gauss(0.0, accuracy / 2)), accuracy)
        bearing = self.rng.uniform(0, 2 * math.pi)
        lat, lng = self.shop_location[shop]
        dlat = distance * math.cos(bearing) / METERS_PER_DEG_LAT
        dlng = distance * math.sin(bearing) / (METERS_PER_DEG_LAT * math.cos(math.radians(lat)))
        return (round(lat + dlat, 6), round(lng + dlng, 6), round(accuracy, 1))

    def basket(self, product: str) -> tuple[str, str, float]:
        options = _BASKETS[self.base_unit[product]]
        qty, unit, size, _ = self.rng.choices(options, weights=[o[3] for o in options])[0]
        return qty, unit, size

    def add(
        self,
        *,
        device: str,
        kind: str,
        ip: str,
        product: str,
        shop: str,
        observed_at: datetime,
        per_base: float,
        quantity: str,
        unit: str,
        size: float,
        truth: Truth,
        gps_rate: float,
        error_mode: str | None = None,
    ) -> None:
        delay = timedelta(minutes=self.rng.uniform(*SUBMIT_DELAY_MIN))
        self.reports.append(
            SimReport(
                seq=0,
                device_id=device,
                reporter_kind=kind,
                ip_hash=ip,
                client_report_id=uuid.UUID(int=self.rng.getrandbits(128), version=4),
                product=product,
                shop=shop,
                price=_money(per_base * size),
                quantity=Decimal(quantity),
                unit=unit,
                observed_at=observed_at,
                submitted_at=observed_at + delay,
                location=self.location(shop, gps_rate),
                true_price=self.market.shop_price(product, shop, observed_at),
                truth=truth,
                error_mode=error_mode,
            )
        )

    def honest_truth(self, product: str, shop: str, t: datetime) -> Truth:
        if self.market.gouging(product, shop, t) != 1.0:
            return Truth.SHOP_OUTLIER
        if self.market.in_shift(product, t):
            return Truth.MARKET_SHIFT
        return Truth.NORMAL

    # ---- reporter kinds
    def crowd(self, kind: str, count: int, rate: float, noise: float, error_rate: float) -> None:
        sloppy = self.s.reporters.sloppy
        modes = list(sloppy.unit_error_modes)
        mode_weights = list(sloppy.unit_error_modes.values())
        for i in range(count):
            device, ip = f"sim-{kind}-{i:03d}", f"sim-ip-{kind}-{i:03d}"
            for day in range(self.s.days):
                for _ in range(_poisson(self.rng, rate)):
                    t = self.shopping_time(day)
                    product = self.rng.choices(self.s.products, weights=self.product_weights)[0]
                    shop = self.rng.choice(self.s.shops)
                    price = self.market.shop_price(product, shop, t)
                    price *= math.exp(self.rng.gauss(0.0, noise))
                    if self.rng.random() < error_rate:
                        mode = self.rng.choices(modes, weights=mode_weights)[0]
                        qty, unit, size = self.unit_error(product, mode)
                        self.add(
                            device=device,
                            kind=kind,
                            ip=ip,
                            product=product,
                            shop=shop,
                            observed_at=t,
                            per_base=price,
                            quantity=qty,
                            unit=unit,
                            size=size,
                            truth=Truth.BAD_UNIT,
                            gps_rate=self.s.location.gps_rate,
                            error_mode=mode,
                        )
                    else:
                        qty, unit, size = self.basket(product)
                        self.add(
                            device=device,
                            kind=kind,
                            ip=ip,
                            product=product,
                            shop=shop,
                            observed_at=t,
                            per_base=price,
                            quantity=qty,
                            unit=unit,
                            size=size,
                            truth=self.honest_truth(product, shop, t),
                            gps_rate=self.s.location.gps_rate,
                        )

    def unit_error(self, product: str, mode: str) -> tuple[str, str, float]:
        """(typed quantity, typed unit, size actually paid for in base units)."""
        whole = {BaseUnit.KG: "kg", BaseUnit.L: "l", BaseUnit.DOZEN: "dozen"}
        base = self.base_unit[product]
        if mode == "half":  # paid for half a unit, typed one unit
            return "1", whole[base], 0.5
        if mode == "double":  # paid for two units, typed one
            return "1", whole[base], 2.0
        if base is BaseUnit.DOZEN:  # a per-piece price typed per dozen
            return "1", "dozen", 1 / 12
        small = {BaseUnit.KG: "g", BaseUnit.L: "ml"}
        return "1", small[base], 1.0  # the per-kg price typed for "1 g"

    def malicious(self) -> None:
        cfg = self.s.reporters.malicious
        for i in range(cfg.count):
            device, ip = f"sim-malicious-{i:03d}", f"sim-ip-malicious-{i:03d}"
            for day in range(self.s.days):
                for _ in range(_poisson(self.rng, cfg.reports_per_day)):
                    t = self.shopping_time(day)
                    product = self.rng.choices(self.s.products, weights=self.product_weights)[0]
                    shop = cfg.target_shop or self.rng.choice(self.s.shops)
                    price = self.market.shop_price(product, shop, t) * (1 + cfg.inflate)
                    qty, unit, size = self.basket(product)
                    # Made-up reports: nobody stood in the shop, so no GPS fix.
                    self.add(
                        device=device,
                        kind="malicious",
                        ip=ip,
                        product=product,
                        shop=shop,
                        observed_at=t,
                        per_base=price,
                        quantity=qty,
                        unit=unit,
                        size=size,
                        truth=Truth.MALICIOUS,
                        gps_rate=0.0,
                    )

    def spammers(self) -> None:
        cfg = self.s.reporters.spammer
        for i in range(cfg.count):
            device, ip = f"sim-spammer-{i:03d}", f"sim-ip-spammer-{i:03d}"
            product = cfg.product or self.rng.choice(self.s.products)
            shop = cfg.shop or self.rng.choice(self.s.shops)
            start = self.market.local(self.s.start + timedelta(days=cfg.day), cfg.time)
            n = round(cfg.reports_per_hour * cfg.hours)
            qty, unit, _, _ = _BASKETS[self.base_unit[product]][0]
            for k in range(n):
                t = start + timedelta(hours=cfg.hours * k / n)
                price = self.market.shop_price(product, shop, t) * (1 + cfg.inflate)
                price *= math.exp(self.rng.gauss(0.0, 0.05))
                self.add(
                    device=device,
                    kind="spammer",
                    ip=ip,
                    product=product,
                    shop=shop,
                    observed_at=t,
                    per_base=price,
                    quantity=qty,
                    unit=unit,
                    size=1.0,
                    truth=Truth.MALICIOUS,
                    gps_rate=0.0,
                )


def generate(scenario: Scenario, catalog: SeedCatalog) -> SimStream:
    """All reports of the scenario, ordered by submission time (``seq`` = position)."""
    area = next((a for a in catalog.areas if a.id == scenario.area), None)
    if area is None:
        raise ValueError(f"area {scenario.area!r} is not in the catalog")
    market = Market(scenario, ZoneInfo(area.timezone))
    b = _Builder(scenario, catalog, market)
    r = scenario.reporters
    b.crowd("honest", r.honest.count, r.honest.reports_per_day, r.honest.noise_sigma, 0.0)
    b.crowd(
        "sloppy",
        r.sloppy.count,
        r.sloppy.reports_per_day,
        r.sloppy.noise_sigma,
        r.sloppy.unit_error_rate,
    )
    b.malicious()
    b.spammers()
    ordered = sorted(b.reports, key=lambda rep: (rep.submitted_at, rep.device_id))
    reports = [replace(rep, seq=i) for i, rep in enumerate(ordered)]
    half_lives = {p.id: p.half_life_h for p in catalog.products if p.id in scenario.products}
    return SimStream(scenario, reports, event_windows(scenario, market), market, half_lives)
