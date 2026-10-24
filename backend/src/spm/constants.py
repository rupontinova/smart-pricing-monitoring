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

# M06 — guardrails
OBSERVED_AT_MAX_AGE_H: Final = 168  # 7 days
OBSERVED_AT_FUTURE_SKEW_MIN: Final = 5
GEOFENCE_BASE_M: Final = 300
GEOFENCE_ACCURACY_ALLOWANCE_MAX_M: Final = 200  # allowed = base + min(accuracy, this)
DEDUPE_WINDOW_H: Final = 6
DEDUPE_PRICE_TOL: Final = Decimal("0.01")  # relative difference
RATE_LIMIT_PER_HOUR: Final = 30  # reports per reporter
RATE_LIMIT_PER_DAY: Final = 100
UNIT_HINT_MAX: Final = 2  # "did you mean" hints returned

# M07 — event backbone
EVENT_MAX_DELIVERIES: Final = 5  # attempts before an event goes to the DLQ
OUTBOX_SWEEP_AGE_S: Final = 30  # republish outbox rows unpublished for longer than this
OUTBOX_SWEEP_BATCH: Final = 100
STREAM_MAXLEN: Final = 100_000  # approximate cap on spm:events
STREAM_READ_COUNT: Final = 10
STREAM_BLOCK_MS: Final = 5_000
STREAM_CLAIM_INTERVAL_S: Final = 30  # how often a consumer runs XAUTOCLAIM
STREAM_CLAIM_MIN_IDLE_MS: Final = 60_000  # reclaim entries pending (un-ACKed) for longer

# M08 — device auth & reports
AUTH_RATE_LIMIT_PER_HOUR_PER_IP: Final = 20
LAST_SEEN_UPDATE_INTERVAL_H: Final = 1  # reporters.last_seen_at is written at most this often

# M09 — robust statistics
MAD_SCALE: Final = 1.4826  # MAD x this ~ standard deviation for normal data
MAD_FLOOR_FRAC: Final = 0.03  # MAD_eff >= 3% of the center (M14 tuning; plan: 0.02)
HALF_LIFE_MIN_H: Final = 24
HALF_LIFE_MAX_H: Final = 504  # 21 days
HALF_LIFE_VOLATILITY_K: Final = 0.10  # half_life_days = K / sigma_daily
HALF_LIFE_MIN_RETURNS: Final = 10  # daily log returns needed to estimate volatility

# M10 — trust engine
TRUST_PRIOR_ALPHA: Final = 1.0  # Beta(α, β) prior for a new reporter: trust 0.5
TRUST_PRIOR_BETA: Final = 1.0
HIGH_TRUST_MIN_SCORE: Final = 0.90  # M14 tuning; plan: 0.80
HIGH_TRUST_MIN_EVIDENCE: Final = 15  # α + β − 2 ≥ this (M14 tuning; plan: 10)
NO_LOCATION_WEIGHT: Final = 0.5  # weight factor when the location is not verified
AGREE_LOG_TOL: Final = 0.05  # |ln(p1/p2)| ≤ tol counts as agreement
DISAGREE_LOG_TOL: Final = 0.10  # |ln(p1/p2)| > tol counts as contradiction
CORROBORATION_MIN_H: Final = 48  # W = max(48 h, 0.5 × half-life) (M14 tuning; plan: 6)
INDEPENDENCE_MIN_GAP_MIN: Final = 10  # same IP hash within 10 min → not independent
Z_RESOLVE_NORMAL: Final = 2.0  # |z_market| ≤ this "agrees with the market"

# M11 — anomaly classifier
BOUNDS_LOW_MULT: Final = 0.2  # relative bounds vs the 90-day area median
BOUNDS_HIGH_MULT: Final = 5.0
BOUNDS_WINDOW_D: Final = 90
BOUNDS_MIN_N: Final = 5
Z_THRESHOLD: Final = 3.0
SHOP_HISTORY_D: Final = 30
SHOP_HISTORY_MIN_N: Final = 4
MARKET_MIN_N: Final = 5
MARKET_WINDOW_MIN_D: Final = 3  # market window = max(3 d, 2 × half-life)
MARKET_SHIFT_WINDOW_H: Final = 24  # M14 tuning; plan: 48
MARKET_SHIFT_MIN_SHOPS: Final = 3  # and ≥ 50% of the area's active shops

# M12 — fair-price band & hot cache
BAND_WINDOW_HALF_LIVES: Final = 4
BAND_WINDOW_MAX_D: Final = 60
BAND_MAX_SHOP_SHARE: Final = 0.40
CONF_HIGH_WEIGHT: Final = 3.0
CONF_MEDIUM: Final = 1.5
CONF_LOW: Final = 0.5
CONF_HIGH_MIN_SHOPS: Final = 3
SHOP_PRICE_MIN_FRESHNESS: Final = 0.25  # shop price shown only if its age ≤ 2 half-lives
CACHE_TTL_MIN_H: Final = 24  # hot-cache TTL = max(2 × half-life, 24 h)

# M17 — maintenance (added early: the M14 simulator emulates the nightly auto-resolve)
ANOMALY_AUTO_RESOLVE_D: Final = 7
