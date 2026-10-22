from decimal import Decimal

import pytest

from spm.core.enums import BaseUnit, Dimension
from spm.core.errors import UnknownUnit
from spm.core.units import BASE_DIMENSION, UNIT_SYNONYMS, UNITS, parse_unit, units_for


@pytest.mark.parametrize(
    ("text", "canonical"),
    [
        ("g", "g"), ("GM", "g"), ("gms.", "g"), (" Grams ", "g"),
        ("kg", "kg"), ("Kgs", "kg"), ("kilo", "kg"), ("kilogram.", "kg"),
        ("ml", "ml"), ("millilitre", "ml"), ("milliliter", "ml"),
        ("L", "l"), ("ltr", "l"), ("litres", "l"), ("liter", "l"),
        ("pc", "piece"), ("PCS", "piece"), ("egg", "piece"), ("eggs", "piece"),
        ("hali", "hali"), ("dz", "dozen"), ("Doz.", "dozen"), ("tray30", "tray"),
    ],
)  # fmt: skip
def test_parse_unit_maps_synonyms(text: str, canonical: str) -> None:
    assert parse_unit(text) == canonical


@pytest.mark.parametrize("text", ["", "bag", "kgg", "500g", "lb", ".", "k g"])
def test_parse_unit_rejects_unknown(text: str) -> None:
    with pytest.raises(UnknownUnit) as info:
        parse_unit(text)
    assert info.value.extra["known_units"] == sorted(UNITS)


def test_every_synonym_targets_a_known_unit() -> None:
    assert set(UNIT_SYNONYMS.values()) == set(UNITS)
    assert all(UNIT_SYNONYMS[u] == u for u in UNITS)  # canonical names parse to themselves


def test_unit_factors() -> None:
    assert UNITS["g"] == (Dimension.MASS, Decimal("0.001"))
    assert UNITS["piece"] == (Dimension.COUNT, Decimal("0.08333333333"))
    assert UNITS["hali"] == (Dimension.COUNT, Decimal("0.3333333333"))
    assert UNITS["tray"] == (Dimension.COUNT, Decimal("2.5"))
    # Each base unit is a unit of its own dimension with factor 1.
    for base, dimension in BASE_DIMENSION.items():
        assert UNITS[base.value] == (dimension, Decimal(1))


def test_units_for_dimension_smallest_first() -> None:
    assert units_for(Dimension.MASS) == ["g", "kg"]
    assert units_for(Dimension.COUNT) == ["piece", "hali", "dozen", "tray"]
    assert set(BASE_DIMENSION) == set(BaseUnit)
