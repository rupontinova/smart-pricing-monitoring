"""Tunable algorithm constants for Smart Pricing Monitor.

Every threshold used by the domain logic lives here as a module-level ``Final``, so tests and the
simulator can override them in one place. Values and their meaning are specified in
``doc/development/MODULES.md`` §2.7; each constant is added by the module that first uses it.
"""

from typing import Final

# M04 — catalog
CATALOG_REFRESH_S: Final = 300  # in-process catalog cache lifetime
NEARBY_RADIUS_M: Final = 2000  # default radius for "shops near me" (also M15)
