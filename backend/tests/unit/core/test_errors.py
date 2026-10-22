import re
from pathlib import Path

import pytest

from spm.core.errors import ERROR_TYPES, RateLimited, SpmError

MODULES_MD = Path(__file__).parents[4] / "doc" / "development" / "MODULES.md"


def _registry() -> dict[str, int]:
    """The §2.5 error code registry, parsed from MODULES.md (the source of truth)."""
    text = MODULES_MD.read_text(encoding="utf-8")
    section = text[text.index("**Error code registry**") : text.index("### 2.6")]
    return {
        code: int(status)
        for code, status in re.findall(r"^\| `([A-Z_]+)` \| (\d{3}) \|", section, re.M)
    }


REGISTRY = _registry()


def _all_subclasses(cls: type[SpmError]) -> set[type[SpmError]]:
    found: set[type[SpmError]] = set()
    for sub in cls.__subclasses__():
        found |= {sub} | _all_subclasses(sub)
    return {c for c in found if c.__module__ == "spm.core.errors"}


def test_registry_parsed() -> None:
    assert len(REGISTRY) >= 14
    assert REGISTRY["PRICE_OUT_OF_BOUNDS"] == 422


@pytest.mark.parametrize("error_type", ERROR_TYPES, ids=lambda t: t.__name__)
def test_error_code_and_status_match_registry(error_type: type[SpmError]) -> None:
    assert error_type.code in REGISTRY
    assert error_type.status == REGISTRY[error_type.code]


def test_error_codes_unique() -> None:
    codes = [t.code for t in ERROR_TYPES]
    assert len(codes) == len(set(codes))


def test_every_registry_code_has_one_subclass() -> None:
    assert {t.code for t in ERROR_TYPES} == set(REGISTRY)


def test_error_types_lists_every_subclass() -> None:
    assert _all_subclasses(SpmError) == set(ERROR_TYPES)


def test_error_carries_detail_and_extra() -> None:
    err = RateLimited("slow down", retry_after_s=30, scope="reporter")
    assert err.detail == "slow down"
    assert err.retry_after_s == 30
    assert err.extra == {"retry_after_s": 30, "scope": "reporter"}
    assert str(err) == "slow down"
    assert str(RateLimited(retry_after_s=1)) == "Too many requests"
