from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from spm.constants import BASE_QUANTITY_MAX, BASE_QUANTITY_MIN
from spm.core.enums import BaseUnit
from spm.core.errors import (
    QuantityOutOfRange,
    SpmError,
    UnitMismatch,
    UnknownProduct,
    UnknownUnit,
    ValidationError,
)
from spm.core.models import Product, RawPriceInput
from spm.core.normalizer import levenshtein, normalize, resolve_product, similarity
from spm.core.units import UNIT_SYNONYMS, UNITS
from tests.unit.core.catalog_fixture import CATALOG


def _raw(product: str, price: str, quantity: str, unit: str) -> RawPriceInput:
    """``product`` is an id if it is one in the catalog, otherwise free text."""
    is_id = CATALOG.product(product) is not None
    return RawPriceInput(
        product_id=product if is_id else None,
        product_text=None if is_id else product,
        price=Decimal(price),
        quantity=Decimal(quantity),
        unit=unit,
    )


# ------------------------------------------------------------------ the plan's table (M05)
@pytest.mark.parametrize(
    ("product", "price", "quantity", "unit", "product_id", "norm_price", "base_unit", "base_qty"),
    [
        ("onion", "40", "500", "gm", "onion", "80.0000", BaseUnit.KG, "0.5"),
        ("Piyaz", "75", "1", "kg", "onion", "75.0000", BaseUnit.KG, "1"),
        ("eggs", "12", "1", "piece", "eggs", "144.0000", BaseUnit.DOZEN, "0.08333333333"),
        ("eggs", "50", "1", "hali", "eggs", "150.0000", BaseUnit.DOZEN, "0.3333333333"),
        ("eggs", "330", "1", "tray", "eggs", "132.0000", BaseUnit.DOZEN, "2.5"),
        ("milk", "45", "500", "ml", "milk", "90.0000", BaseUnit.L, "0.5"),
        ("rice", "3000", "50", "kg", "rice", "60.0000", BaseUnit.KG, "50"),
        ("oniun", "40", "1", "kg", "onion", "40.0000", BaseUnit.KG, "1"),
    ],
)
def test_normalize_table(
    product: str, price: str, quantity: str, unit: str,
    product_id: str, norm_price: str, base_unit: BaseUnit, base_qty: str,
) -> None:  # fmt: skip
    result = normalize(_raw(product, price, quantity, unit), CATALOG)
    assert result.product_id == product_id
    assert result.norm_price == Decimal(norm_price)
    assert str(result.norm_price) == norm_price  # quantized to 4 dp
    assert result.base_unit is base_unit
    assert result.base_quantity == Decimal(base_qty)


def test_normalize_unit_mismatch() -> None:
    with pytest.raises(UnitMismatch) as info:
        normalize(_raw("onion", "40", "1", "l"), CATALOG)
    assert info.value.extra == {
        "unit": "l", "product_id": "onion", "base_unit": "kg", "allowed_units": ["g", "kg"],
    }  # fmt: skip


def test_normalize_quantity_out_of_range_below() -> None:
    with pytest.raises(QuantityOutOfRange) as info:
        normalize(_raw("onion", "40", "10", "g"), CATALOG)
    assert info.value.extra["base_quantity"] == pytest.approx(0.01)
    assert "0.01 kg" in info.value.detail


def test_normalize_quantity_out_of_range_above() -> None:
    with pytest.raises(QuantityOutOfRange):
        normalize(_raw("rice", "3000", "51", "kg"), CATALOG)


def test_normalize_quantity_bounds_inclusive() -> None:
    assert normalize(_raw("onion", "5", "50", "g"), CATALOG).base_quantity == BASE_QUANTITY_MIN
    assert normalize(_raw("rice", "3000", "50", "kg"), CATALOG).base_quantity == BASE_QUANTITY_MAX


def test_unknown_product_has_suggestions() -> None:
    with pytest.raises(UnknownProduct) as info:
        normalize(_raw("xyz", "40", "1", "kg"), CATALOG)
    suggestions = info.value.extra["suggestions"]
    assert isinstance(suggestions, list)
    assert 1 <= len(suggestions) <= 3
    assert info.value.extra["query"] == "xyz"


def test_unknown_unit_raised_after_product_resolves() -> None:
    with pytest.raises(UnknownUnit):
        normalize(_raw("onion", "40", "1", "bag"), CATALOG)


@pytest.mark.parametrize(
    ("price", "quantity", "field"),
    [("0", "1", "price"), ("-5", "1", "price"), ("40.505", "1", "price"), ("NaN", "1", "price"),
     ("Infinity", "1", "price"), ("40", "0", "quantity"), ("40", "-1", "quantity")],
)  # fmt: skip
def test_invalid_amounts_rejected(price: str, quantity: str, field: str) -> None:
    with pytest.raises(ValidationError) as info:
        normalize(_raw("onion", price, quantity, "kg"), CATALOG)
    assert info.value.extra["fields"] == [field]


def test_price_with_trailing_zeros_is_two_decimals() -> None:
    assert normalize(_raw("onion", "40.500", "1", "kg"), CATALOG).norm_price == Decimal("40.5")


def test_norm_price_rounds_half_even() -> None:
    # Exact ties at the 5th decimal: 0.01 / 0.32 kg = 0.03125 and 0.03 / 0.32 kg = 0.09375.
    # Half-even gives 0.0312 and 0.0938 (half-up would give 0.0313 and 0.0938).
    assert normalize(_raw("onion", "0.01", "320", "g"), CATALOG).norm_price == Decimal("0.0312")
    assert normalize(_raw("onion", "0.03", "320", "g"), CATALOG).norm_price == Decimal("0.0938")


# ------------------------------------------------------------------------ product resolution
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("piyaz", "onion"), ("  PIYAZ!! ", "onion"), ("Mosur Dal", "lentils"),
        ("onion", "onion"), ("Rice (coarse)", "rice"), ("red lentils", "lentils"),
        ("tomatto", "tomato"), ("potatoe", "potato"), ("piyaaz", "onion"), ("soybean oyl", "oil"),
    ],
)  # fmt: skip
def test_resolve_product_by_text(text: str, expected: str) -> None:
    assert resolve_product(None, text, CATALOG).id == expected


def test_resolve_product_by_id() -> None:
    assert resolve_product("eggs", "ignored text", CATALOG).id == "eggs"


@pytest.mark.parametrize("product_id", ["nope", "Onion"])
def test_resolve_unknown_product_id(product_id: str) -> None:
    with pytest.raises(UnknownProduct):
        resolve_product(product_id, None, CATALOG)


def test_short_text_never_fuzzy_resolves_but_suggests() -> None:
    with pytest.raises(UnknownProduct) as info:
        resolve_product(None, "chai", CATALOG)  # 1 edit from "chal" (rice), but too short
    assert info.value.extra["suggestions"][0] == "rice"  # type: ignore[index]


@pytest.mark.parametrize("text", [None, "", "  ", "!!!"])
def test_missing_product_is_validation_error(text: str | None) -> None:
    with pytest.raises(ValidationError):
        resolve_product(None, text, CATALOG)


class _Catalog:
    def __init__(self, products: list[Product], aliases: dict[str, str] | None = None) -> None:
        self._products, self._aliases = products, aliases or {}

    def product(self, product_id: str) -> Product | None:
        return next((p for p in self._products if p.id == product_id), None)

    def products(self) -> list[Product]:
        return self._products

    def alias_map(self) -> dict[str, str]:
        return self._aliases


def _product(pid: str, name: str, *, active: bool = True) -> Product:
    return Product(pid, name, BaseUnit.KG, "veg", 48, Decimal(1), Decimal(100), active)


def test_inactive_products_never_resolve() -> None:
    catalog = _Catalog([_product("garlic", "Garlic", active=False)], {"rosun": "garlic"})
    for pid, text in [("garlic", None), (None, "rosun"), (None, "garlic"), (None, "garlik")]:
        with pytest.raises(UnknownProduct):
            resolve_product(pid, text, catalog)


def test_fuzzy_tie_does_not_resolve() -> None:
    catalog = _Catalog([_product("aaaab", "aaaab"), _product("aaaac", "aaaac")])
    with pytest.raises(UnknownProduct) as info:
        resolve_product(None, "aaaad", catalog)  # 90 against both
    assert info.value.extra["suggestions"] == ["aaaab", "aaaac"]


# ---------------------------------------------------------------------------- similarity
@pytest.mark.parametrize(
    ("a", "b", "distance"),
    [("", "", 0), ("abc", "", 3), ("kitten", "sitting", 3), ("oniun", "onion", 1), ("ab", "ba", 2)],
)
def test_levenshtein(a: str, b: str, distance: int) -> None:
    assert levenshtein(a, b) == distance
    assert levenshtein(b, a) == distance


def test_similarity_scale() -> None:
    assert similarity("onion", "onion") == 100
    assert similarity("", "") == 100
    assert similarity("oniun", "onion") == 90
    assert similarity("abc", "xyz") == 50


# ---------------------------------------------------------------------------- properties
ID_PRODUCTS = [p for p in CATALOG.products() if p.active]
SPELLINGS: dict[str, list[str]] = {}
for _spelling, _canonical in UNIT_SYNONYMS.items():
    SPELLINGS.setdefault(_canonical, []).append(_spelling)

prices = st.decimals(min_value=Decimal("0.01"), max_value=Decimal("1000000"), places=2)


@st.composite
def valid_inputs(draw: st.DrawFn) -> RawPriceInput:
    product = draw(st.sampled_from(ID_PRODUCTS))
    unit = draw(
        st.sampled_from(
            [u for u, (dim, _) in UNITS.items() if dim.value == _dimension(product.base_unit)]
        )
    )
    factor = UNITS[unit][1]
    low, high = BASE_QUANTITY_MIN / factor, BASE_QUANTITY_MAX / factor
    quantity = draw(
        st.decimals(min_value=low, max_value=high, places=3).filter(
            lambda q: BASE_QUANTITY_MIN <= q * factor <= BASE_QUANTITY_MAX
        )
    )
    return RawPriceInput(product.id, None, draw(prices), quantity, unit)


def _dimension(base: BaseUnit) -> str:
    from spm.core.units import BASE_DIMENSION

    return BASE_DIMENSION[base].value


@given(valid_inputs())
def test_norm_price_times_base_quantity_recovers_price(raw: RawPriceInput) -> None:
    result = normalize(raw, CATALOG)
    assert abs(result.norm_price * result.base_quantity - raw.price) <= Decimal("0.01")
    assert result.norm_price == result.norm_price.quantize(Decimal("0.0001"))


@given(valid_inputs(), st.data())
def test_result_independent_of_unit_spelling(raw: RawPriceInput, data: st.DataObject) -> None:
    canonical = raw.unit
    spelling = data.draw(st.sampled_from(SPELLINGS[canonical]))
    decorated = data.draw(
        st.sampled_from([spelling, spelling.upper(), f"  {spelling}. ", spelling.title()])
    )
    reference = normalize(raw, CATALOG)
    other = normalize(
        RawPriceInput(raw.product_id, None, raw.price, raw.quantity, decorated), CATALOG
    )
    assert (other.norm_price, other.base_quantity, other.product_id) == (
        reference.norm_price,
        reference.base_quantity,
        reference.product_id,
    )


@given(st.text(max_size=30))
def test_resolve_never_crashes_on_arbitrary_text(text: str) -> None:
    try:
        resolve_product(None, text, CATALOG)
    except SpmError as exc:
        assert exc.code in {"UNKNOWN_PRODUCT", "VALIDATION_ERROR"}
