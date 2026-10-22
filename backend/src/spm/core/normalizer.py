"""Normalizer (badge B): whatever the user typed -> a canonical price per base unit.

"piyaz", 40, 500, "gm"  ->  onion, 80.0000 per kg (base_quantity 0.5)
"""

import re
import unicodedata
from collections.abc import Iterable, Mapping
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Protocol

from spm.constants import (
    BASE_QUANTITY_MAX,
    BASE_QUANTITY_MIN,
    FUZZY_MATCH_MIN_SCORE,
    FUZZY_MAX_SUGGESTIONS,
    FUZZY_MIN_LENGTH,
    FUZZY_SUGGEST_MIN_SCORE,
)
from spm.core.errors import QuantityOutOfRange, UnitMismatch, UnknownProduct, ValidationError
from spm.core.models import NormalizedPrice, Product, RawPriceInput
from spm.core.units import BASE_DIMENSION, UNITS, parse_unit, units_for

NORM_PRICE_QUANTUM = Decimal("0.0001")
PRICE_QUANTUM = Decimal("0.01")

_SPACES = re.compile(r"\s+")


class ProductCatalog(Protocol):
    """What the normalizer needs from the catalog (``CatalogCache`` satisfies it)."""

    def product(self, product_id: str) -> Product | None: ...
    def products(self) -> list[Product]: ...
    def alias_map(self) -> Mapping[str, str]: ...


# ----------------------------------------------------------------------------------- text
def _keep(ch: str) -> bool:
    # Letters and digits of any script, plus combining marks: Bangla/Devanagari vowel signs are
    # marks (Mn/Mc), and dropping them would mangle words such as "পেঁয়াজ".
    return ch.isalnum() or ch.isspace() or unicodedata.category(ch).startswith("M")


def normalize_text(s: str) -> str:
    """NFKC, casefold, keep only letters, digits and spaces (any script), collapse whitespace."""
    text = unicodedata.normalize("NFKC", s).casefold()
    return _SPACES.sub(" ", "".join(ch for ch in text if _keep(ch))).strip()


def levenshtein(a: str, b: str) -> int:
    """Edit distance with unit costs for insert, delete and substitute."""
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def similarity(a: str, b: str) -> float:
    """Levenshtein ratio on a 0-100 scale: ``100 * (1 - d / (len(a) + len(b)))``.

    One typo scores 90 in a 5-letter word, 87.5 in 4 letters and 83.3 in 3. Fuzzy resolution also
    needs ``FUZZY_MIN_LENGTH`` characters, so in practice one typo resolves in words of 5+ letters.
    """
    total = len(a) + len(b)
    if total == 0:
        return 100.0
    return 100.0 * (1 - levenshtein(a, b) / total)


# -------------------------------------------------------------------------------- product
def _names(product: Product) -> set[str]:
    return {normalize_text(product.id), normalize_text(product.name), *product.aliases}


def _scores(text: str, products: Iterable[Product]) -> list[tuple[float, str]]:
    """(best score over the product's id, name and aliases, product id), best first."""
    scored = [(max(similarity(text, n) for n in _names(p)), p.id) for p in products]
    return sorted(scored, key=lambda s: (-s[0], s[1]))


def _unknown(text: str, catalog: ProductCatalog) -> UnknownProduct:
    active = [p for p in catalog.products() if p.active]
    suggestions = [
        pid
        for score, pid in _scores(normalize_text(text), active)
        if score >= FUZZY_SUGGEST_MIN_SCORE
    ][:FUZZY_MAX_SUGGESTIONS]
    return UnknownProduct(f"No product matches {text!r}.", query=text, suggestions=suggestions)


def resolve_product(
    product_id: str | None, product_text: str | None, catalog: ProductCatalog
) -> Product:
    """Find the product by id, else by alias / id / name, else by a single clear fuzzy match."""
    if product_id:
        product = catalog.product(product_id)
        if product is None or not product.active:
            raise _unknown(product_id, catalog)
        return product

    text = normalize_text(product_text or "")
    if not text:
        raise ValidationError("Give a product_id or a product name.", fields=["product_text"])

    active = [p for p in catalog.products() if p.active]
    by_id = {p.id: p for p in active}

    # Exact alias, then exact id or name.
    aliased = catalog.alias_map().get(text)
    if aliased in by_id:
        return by_id[aliased]
    for p in active:
        if text in (normalize_text(p.id), normalize_text(p.name)):
            return p

    # Fuzzy: resolve only on a single best candidate at or above the threshold, and only for
    # text long enough that one typo can't turn one short word into another ("chai" -> "chal").
    scores = _scores(text, active)
    if len(text) >= FUZZY_MIN_LENGTH and scores and scores[0][0] >= FUZZY_MATCH_MIN_SCORE:
        tied = [pid for score, pid in scores if score == scores[0][0]]
        if len(tied) == 1:
            return by_id[tied[0]]
    raise _unknown(product_text or "", catalog)


# ---------------------------------------------------------------------------------- price
def _validate_amounts(price: Decimal, quantity: Decimal) -> None:
    if not price.is_finite() or price <= 0:
        raise ValidationError("Price must be greater than 0.", fields=["price"])
    if price != price.quantize(PRICE_QUANTUM):
        raise ValidationError("Price can have at most 2 decimal places.", fields=["price"])
    if not quantity.is_finite() or quantity <= 0:
        raise ValidationError("Quantity must be greater than 0.", fields=["quantity"])


def normalize(raw: RawPriceInput, catalog: ProductCatalog) -> NormalizedPrice:
    """Validate and convert a raw price to a price per base unit of the resolved product."""
    _validate_amounts(raw.price, raw.quantity)
    product = resolve_product(raw.product_id, raw.product_text, catalog)

    unit = parse_unit(raw.unit)
    dimension, factor = UNITS[unit]
    expected = BASE_DIMENSION[product.base_unit]
    if dimension is not expected:
        raise UnitMismatch(
            f"{product.name} is sold by {product.base_unit.value}; {raw.unit!r} measures "
            f"{dimension.value.lower()}.",
            unit=unit,
            product_id=product.id,
            base_unit=product.base_unit.value,
            allowed_units=units_for(expected),
        )

    base_quantity = raw.quantity * factor
    if not BASE_QUANTITY_MIN <= base_quantity <= BASE_QUANTITY_MAX:
        raise QuantityOutOfRange(
            f"{raw.quantity} {unit} is {base_quantity.normalize()} {product.base_unit.value}; "
            f"allowed is {BASE_QUANTITY_MIN} to {BASE_QUANTITY_MAX} {product.base_unit.value}.",
            base_quantity=float(base_quantity),
            min=float(BASE_QUANTITY_MIN),
            max=float(BASE_QUANTITY_MAX),
            base_unit=product.base_unit.value,
        )

    norm_price = (raw.price / base_quantity).quantize(NORM_PRICE_QUANTUM, rounding=ROUND_HALF_EVEN)
    return NormalizedPrice(
        product_id=product.id,
        base_unit=product.base_unit,
        base_quantity=base_quantity,
        norm_price=norm_price,
        raw=raw,
    )
