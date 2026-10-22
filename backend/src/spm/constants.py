"""Tunable algorithm constants for Smart Pricing Monitor.

Every threshold used by the domain logic lives here as a module-level ``Final``, so tests and the
simulator can override them in one place. Values and their meaning are specified in
``doc/development/MODULES.md`` §2.7; each constant is added by the module that first uses it.
"""

from decimal import Decimal
from typing import Final

# M04 — catalog
CATALOG_REFRESH_S: Final = 300  # in-process catalog cache lifetime
NEARBY_RADIUS_M: Final = 2000  # default radius for "shops near me" (also M15)

# M05 — normalizer
BASE_QUANTITY_MIN: Final = Decimal("0.05")  # in base units (kg / l / dozen)
BASE_QUANTITY_MAX: Final = Decimal("50")
FUZZY_MATCH_MIN_SCORE: Final = 85  # a single best fuzzy candidate at or above this resolves
FUZZY_MIN_LENGTH: Final = 5  # shorter text never auto-resolves by fuzzy match (only suggests)
FUZZY_SUGGEST_MIN_SCORE: Final = 60  # UnknownProduct suggests products scoring at least this
FUZZY_MAX_SUGGESTIONS: Final = 3
