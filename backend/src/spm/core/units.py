"""Units: surface spellings, canonical units and their factor to the product's base unit."""

from decimal import Context, Decimal

from spm.core.enums import BaseUnit, Dimension
from spm.core.errors import UnknownUnit

# Unit fractions are stored at 10 significant digits (MODULES.md M05).
_FACTOR_CTX = Context(prec=10)


def _frac(numerator: int, denominator: int) -> Decimal:
    return _FACTOR_CTX.divide(Decimal(numerator), Decimal(denominator))


_SPELLINGS: dict[str, tuple[str, ...]] = {
    "g": ("g", "gm", "gms", "gram", "grams"),
    "kg": ("kg", "kgs", "kilo", "kilos", "kilogram"),
    "ml": ("ml", "mls", "millilitre", "milliliter"),
    "l": ("l", "ltr", "litre", "liter", "liters", "litres"),
    "piece": ("pc", "pcs", "piece", "pieces", "egg", "eggs"),
    "hali": ("hali",),  # a local count unit of 4 pieces
    "dozen": ("dozen", "dz", "doz"),
    "tray": ("tray", "tray30"),  # an egg tray of 30
}

UNIT_SYNONYMS: dict[str, str] = {
    spelling: canonical for canonical, spellings in _SPELLINGS.items() for spelling in spellings
}

UNITS: dict[str, tuple[Dimension, Decimal]] = {
    "g": (Dimension.MASS, _frac(1, 1000)),
    "kg": (Dimension.MASS, Decimal(1)),
    "ml": (Dimension.VOLUME, _frac(1, 1000)),
    "l": (Dimension.VOLUME, Decimal(1)),
    "piece": (Dimension.COUNT, _frac(1, 12)),
    "hali": (Dimension.COUNT, _frac(4, 12)),
    "dozen": (Dimension.COUNT, Decimal(1)),
    "tray": (Dimension.COUNT, _frac(30, 12)),
}

BASE_DIMENSION: dict[BaseUnit, Dimension] = {
    BaseUnit.KG: Dimension.MASS,
    BaseUnit.L: Dimension.VOLUME,
    BaseUnit.DOZEN: Dimension.COUNT,
}


def units_for(dimension: Dimension) -> list[str]:
    """Canonical units of a dimension, smallest first."""
    return sorted((u for u, (d, _) in UNITS.items() if d is dimension), key=lambda u: UNITS[u][1])


def parse_unit(text: str) -> str:
    """Map a typed unit ("Gms.", " KG ") to its canonical unit, or raise ``UnknownUnit``."""
    key = text.strip().lower().removesuffix(".").strip()
    try:
        return UNIT_SYNONYMS[key]
    except KeyError:
        raise UnknownUnit(
            f"{text!r} is not a known unit.", unit=text, known_units=sorted(UNITS)
        ) from None
