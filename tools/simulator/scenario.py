"""Declarative scenarios (``scenarios/*.yaml``), validated with pydantic."""

import datetime as dt
from pathlib import Path
from typing import Annotated, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

SCENARIOS_DIR = Path(__file__).parent / "scenarios"

# Day 0 of every scenario unless it says otherwise: fixed, so a seed always yields the same run.
# Days are area-local calendar days (day 0 starts at local midnight).
DEFAULT_START = dt.date(2026, 1, 5)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MarketShock(_Strict):
    """The whole area's price moves by ``multiplier``, ramping linearly over ``ramp_hours``.

    Starts at local midnight by default: wholesale prices move overnight, before shops open.
    """

    type: Literal["market_shock"]
    day: int = Field(ge=0)
    time: dt.time = dt.time(0)
    product: str
    multiplier: float = Field(gt=0)
    ramp_hours: float = Field(default=24, ge=0)


class ShopGouging(_Strict):
    """One shop charges ``multiplier`` × its usual price for ``duration_days``.

    Starts when the shop opens (08:00 local) by default.
    """

    type: Literal["shop_gouging"]
    day: int = Field(ge=0)
    time: dt.time = dt.time(8)
    product: str
    shop: str
    multiplier: float = Field(gt=0)
    duration_days: float = Field(gt=0)


TrueEvent = Annotated[MarketShock | ShopGouging, Field(discriminator="type")]


class Honest(_Strict):
    count: int = Field(ge=0)
    noise_sigma: float = Field(default=0.02, ge=0)
    reports_per_day: float = Field(default=0.6, ge=0)


UnitErrorMode = Literal["half", "double", "magnitude"]


def _default_error_modes() -> dict[UnitErrorMode, float]:
    return {"half": 0.5, "double": 0.3, "magnitude": 0.2}


class Sloppy(Honest):
    """Honest prices, but ``unit_error_rate`` of the reports carry a quantity/unit mistake."""

    unit_error_rate: float = Field(default=0.2, ge=0, le=1)
    # half: the price of 500 g entered as 1 kg (x0.5); double: 2 kg entered as 1 kg (x2);
    # magnitude: "1 g" typed for 1 kg, or a per-piece egg price entered per dozen (guardrails).
    unit_error_modes: dict[UnitErrorMode, float] = Field(default_factory=_default_error_modes)


class Malicious(_Strict):
    """Reports ``inflate`` above the true price at ``target_shop`` (or any shop)."""

    count: int = Field(ge=0)
    inflate: float = 0.6
    target_shop: str | None = None
    reports_per_day: float = Field(default=0.6, ge=0)


class Spammer(_Strict):
    """One burst of ``reports_per_hour`` from each spammer, starting on ``day``."""

    count: int = Field(default=1, ge=0)
    day: int = Field(default=10, ge=0)
    time: dt.time = dt.time(10)
    hours: float = Field(default=1, gt=0)
    reports_per_hour: float = Field(default=200, gt=0)
    inflate: float = 0.5
    shop: str | None = None
    product: str | None = None


class Reporters(_Strict):
    honest: Honest = Honest(count=40)
    sloppy: Sloppy = Sloppy(count=0)
    malicious: Malicious = Malicious(count=0)
    spammer: Spammer = Spammer(count=0)


class Location(_Strict):
    gps_rate: float = Field(default=0.8, ge=0, le=1)
    accuracy_m: tuple[float, float] = (10, 80)


class Checks(_Strict):
    """Simulated ``/check`` calls: parsed now, replayed from M15 on."""

    per_day: float = Field(default=0, ge=0)
    quote_noise: float = Field(default=0.15, ge=0)


class Targets(_Strict):
    """Acceptance targets (MODULES.md M14); each is checked only when set."""

    bad_unit_recall_min: float | None = None
    shift_detect_within_h: float | None = None
    max_shift_anomalies_per_direction: int | None = None
    outlier_detect_within_h: float | None = None
    no_outlier_on_other_shops: bool = False
    max_false_outliers_per_product_per_30d: float | None = None
    band_mape_max: float | None = None
    band_warmup_days: int = 3


class Scenario(_Strict):
    name: str
    description: str = ""
    days: int = Field(gt=0)
    seed: int = 0
    start: dt.date = DEFAULT_START
    area: str = "demo-central"
    products: list[str] = Field(min_length=1)
    true_price: dict[str, float]
    daily_volatility: dict[str, float] = Field(default_factory=dict)
    shop_premium: dict[str, float]
    # Relative report volume per product (default: equal).
    product_weights: dict[str, float] = Field(default_factory=dict)
    events: list[TrueEvent] = Field(default_factory=list)
    reporters: Reporters = Reporters()
    location: Location = Location()
    checks: Checks = Checks()
    targets: Targets = Targets()

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        missing = [p for p in self.products if p not in self.true_price]
        if missing:
            raise ValueError(f"true_price missing for {missing}")
        if not self.shop_premium:
            raise ValueError("shop_premium must list at least one shop")
        for event in self.events:
            if event.product not in self.products:
                raise ValueError(f"event product {event.product!r} is not in products")
            if isinstance(event, ShopGouging) and event.shop not in self.shop_premium:
                raise ValueError(f"event shop {event.shop!r} is not in shop_premium")
        return self

    @property
    def shops(self) -> list[str]:
        return list(self.shop_premium)


def load_scenario(name_or_path: str | Path) -> Scenario:
    """A scenario by name (``onion_shock``) or by path to a YAML file."""
    path = Path(name_or_path)
    if path.suffix not in (".yaml", ".yml"):
        path = SCENARIOS_DIR / f"{name_or_path}.yaml"
    with path.open(encoding="utf-8") as fh:
        return Scenario.model_validate(yaml.safe_load(fh))


def scenario_names() -> list[str]:
    return sorted(p.stem for p in SCENARIOS_DIR.glob("*.yaml"))
