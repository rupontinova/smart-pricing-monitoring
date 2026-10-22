"""Guardrails (badge C): pure ingest checks. I/O-bound checks run in ``GuardrailService``."""

import math
from datetime import datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal

from spm.constants import (
    BASE_QUANTITY_MAX,
    BASE_QUANTITY_MIN,
    DEDUPE_PRICE_TOL,
    GEOFENCE_ACCURACY_ALLOWANCE_MAX_M,
    GEOFENCE_BASE_M,
    OBSERVED_AT_FUTURE_SKEW_MIN,
    OBSERVED_AT_MAX_AGE_H,
    UNIT_HINT_MAX,
)
from spm.core.errors import ObservedAtOutOfRange, PriceOutOfBounds, TooFarFromShop, UnknownUnit
from spm.core.geo import haversine_m
from spm.core.models import LocationFix, NormalizedPrice, Product, RawPriceInput, Shop, UnitHint
from spm.core.units import UNITS, parse_unit

NORM_PRICE_QUANTUM = Decimal("0.0001")
HINT_QUANTITY_QUANTUM = Decimal("0.001")
# Same unit, quantity scaled: typed the pack count wrong, or g/kg-style off by 1000.
HINT_QUANTITY_SCALES: tuple[Decimal, ...] = (
    Decimal(2), Decimal("0.5"), Decimal(12), 1 / Decimal(12), Decimal(1000), Decimal("0.001"),
)  # fmt: skip
# Two hints whose resulting prices are this close (relative) are the same suggestion.
_SAME_HINT_TOL = 0.01


# --------------------------------------------------------------------------- observed_at
def check_observed_at(
    observed_at: datetime, now: datetime, *, allow_backfill: bool = False
) -> None:
    """Reject observations from the future (beyond clock skew) or older than the max age."""
    if observed_at - now > timedelta(minutes=OBSERVED_AT_FUTURE_SKEW_MIN):
        raise ObservedAtOutOfRange(
            f"observed_at is more than {OBSERVED_AT_FUTURE_SKEW_MIN} minutes in the future.",
            observed_at=observed_at.isoformat(),
            now=now.isoformat(),
        )
    if not allow_backfill and now - observed_at > timedelta(hours=OBSERVED_AT_MAX_AGE_H):
        raise ObservedAtOutOfRange(
            f"observed_at is older than {OBSERVED_AT_MAX_AGE_H // 24} days.",
            observed_at=observed_at.isoformat(),
            now=now.isoformat(),
        )


# --------------------------------------------------------------------------------- bounds
def bounds_reference(product: Product, band_p50: float | None) -> float:
    """The "typical" price used to rank hints: band median, else the bounds' geometric mean."""
    if band_p50 is not None and band_p50 > 0:
        return band_p50
    return math.sqrt(float(product.abs_min) * float(product.abs_max))


def _fmt(q: Decimal) -> str:
    return format(q.normalize(), "f")


def _tidy(q: Decimal) -> Decimal:
    return q.quantize(HINT_QUANTITY_QUANTUM, rounding=ROUND_HALF_EVEN).normalize()


def unit_error_hints(raw: RawPriceInput, product: Product, reference: float) -> list[UnitHint]:
    """Plausible unit/quantity corrections for an implausible price, best first (at most 2).

    Candidates: the same quantity number in another unit of the same dimension, then the same
    unit with the quantity scaled by x2, x0.5, x12, /12, x1000, /1000. A candidate is kept when it
    normalizes cleanly (base quantity in range) and its price lies within the product's bounds.
    Ranked by closeness to ``reference`` on a log scale.
    """
    try:
        unit = parse_unit(raw.unit)
    except UnknownUnit:
        return []
    dimension = UNITS[unit][0]
    candidates: list[tuple[Decimal, str]] = [
        (raw.quantity, other)
        for other, (dim, _) in UNITS.items()
        if dim is dimension and other != unit
    ]
    candidates += [(_tidy(raw.quantity * scale), unit) for scale in HINT_QUANTITY_SCALES]

    kept: list[UnitHint] = []
    for quantity, cand_unit in candidates:
        if quantity <= 0:
            continue
        base_quantity = quantity * UNITS[cand_unit][1]
        if not BASE_QUANTITY_MIN <= base_quantity <= BASE_QUANTITY_MAX:
            continue
        price = (raw.price / base_quantity).quantize(NORM_PRICE_QUANTUM, rounding=ROUND_HALF_EVEN)
        if not product.abs_min <= price <= product.abs_max:
            continue
        if any(
            abs(float(price) / float(h.resulting_norm_price) - 1) <= _SAME_HINT_TOL for h in kept
        ):
            continue  # e.g. "1 piece" and "0.083 dozen" are the same suggestion
        kept.append(
            UnitHint(
                message=(
                    f"Did you mean {_fmt(quantity)} {cand_unit} instead of "
                    f"{_fmt(raw.quantity)} {unit}?"
                ),
                suggested_quantity=quantity,
                suggested_unit=cand_unit,
                resulting_norm_price=price,
            )
        )
    kept.sort(key=lambda h: abs(math.log(float(h.resulting_norm_price) / reference)))
    return kept[:UNIT_HINT_MAX]


def hints_extra(hints: list[UnitHint]) -> list[dict[str, object]]:
    """JSON-friendly form of hints for error extras (``hints`` in the problem body)."""
    return [
        {
            "message": h.message,
            "quantity": float(h.suggested_quantity),
            "unit": h.suggested_unit,
            "norm_price": float(h.resulting_norm_price),
        }
        for h in hints
    ]


def check_absolute_bounds(np: NormalizedPrice, product: Product, reference: float | None) -> None:
    """Raise ``PriceOutOfBounds`` (with hints) when the normalized price is implausible."""
    if product.abs_min <= np.norm_price <= product.abs_max:
        return
    unit = product.base_unit.value
    side = (
        f"below the minimum of {product.abs_min}"
        if np.norm_price < product.abs_min
        else f"above the maximum of {product.abs_max}"
    )
    hints = unit_error_hints(np.raw, product, bounds_reference(product, reference))
    raise PriceOutOfBounds(
        f"{np.norm_price.normalize():f} per {unit} is {side} for {product.name.lower()}.",
        norm_price=float(np.norm_price),
        min=float(product.abs_min),
        max=float(product.abs_max),
        base_unit=unit,
        hints=hints_extra(hints),
    )


# ------------------------------------------------------------------------------- geofence
def geofence(shop: Shop, fix: LocationFix | None) -> tuple[bool, float | None]:
    """(location_verified, distance_m). No fix is accepted unverified; too far is rejected."""
    if fix is None:
        return False, None
    distance = haversine_m(fix.point, shop.location)
    accuracy = max(fix.accuracy_m or 0.0, 0.0)
    allowed = GEOFENCE_BASE_M + min(accuracy, GEOFENCE_ACCURACY_ALLOWANCE_MAX_M)
    if distance > allowed:
        raise TooFarFromShop(
            f"You are {distance:.0f} m from {shop.name}; reports must be within {allowed:.0f} m.",
            distance_m=round(distance, 1),
            allowed_m=round(allowed, 1),
            shop_id=shop.id,
        )
    return True, distance


# ----------------------------------------------------------------------------- duplicates
def is_duplicate(new_price: Decimal, recent: list[Decimal]) -> bool:
    """True if any recent price is within ``DEDUPE_PRICE_TOL`` (relative to the old price)."""
    return any(old > 0 and abs(new_price - old) / old <= DEDUPE_PRICE_TOL for old in recent)
