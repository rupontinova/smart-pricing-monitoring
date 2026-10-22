import pytest

from spm.core.normalizer import normalize_text


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  Piyaz! ", "piyaz"),
        ("Rice (coarse)", "rice coarse"),
        ("MOSUR \t  dal", "mosur dal"),
        ("\uff21\uff2c\uff35", "alu"),  # full-width "ALU" -> NFKC
        ("tray_30", "tray30"),
        ("পেঁয়াজ", "পেঁয়াজ"),  # Bangla, combining marks kept
        ("प्याज़", "प्याज़"),  # Devanagari
        ("Straße", "strasse"),  # casefold
        ("", ""),
    ],
)
def test_normalize_text(raw: str, expected: str) -> None:
    assert normalize_text(raw) == expected


def test_normalize_text_is_idempotent() -> None:
    for s in ["Piyaz!!", "Mosur  Dal", "পেঁয়াজ"]:
        assert normalize_text(normalize_text(s)) == normalize_text(s)
