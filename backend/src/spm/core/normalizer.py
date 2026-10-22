"""Product and price normalization (MODULES.md M05).

``normalize_text`` landed early (M04) because seeding stores aliases in normalized form.
"""

import re
import unicodedata

_SPACES = re.compile(r"\s+")


def _keep(ch: str) -> bool:
    # Letters and digits of any script, plus combining marks: Bangla/Devanagari vowel signs are
    # marks (Mn/Mc), and dropping them would mangle words such as "পেঁয়াজ".
    return ch.isalnum() or ch.isspace() or unicodedata.category(ch).startswith("M")


def normalize_text(s: str) -> str:
    """NFKC, casefold, keep only letters, digits and spaces (any script), collapse whitespace."""
    text = unicodedata.normalize("NFKC", s).casefold()
    return _SPACES.sub(" ", "".join(ch for ch in text if _keep(ch))).strip()
