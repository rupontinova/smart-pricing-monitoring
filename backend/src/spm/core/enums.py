"""Enumerations shared across the domain.

Members whose value isn't spelled out in MODULES.md use their name as the value; this matches the
Postgres enum types (M03) and the API's JSON (e.g. ``"verdict": "HIGH"``).
"""

from enum import StrEnum


class BaseUnit(StrEnum):
    KG = "kg"
    L = "l"
    DOZEN = "dozen"


class Dimension(StrEnum):
    MASS = "MASS"
    VOLUME = "VOLUME"
    COUNT = "COUNT"


class Classification(StrEnum):
    NORMAL = "NORMAL"
    PENDING = "PENDING"
    SHOP_OUTLIER = "SHOP_OUTLIER"
    MARKET_SHIFT = "MARKET_SHIFT"
    BAD_REPORT = "BAD_REPORT"
    EXPIRED = "EXPIRED"


class ScoringState(StrEnum):
    QUEUED = "queued"
    SCORED = "scored"
    FAILED = "failed"


class AnomalyKind(StrEnum):
    SHOP_OUTLIER = "SHOP_OUTLIER"
    MARKET_SHIFT = "MARKET_SHIFT"
    BAD_REPORT = "BAD_REPORT"


class Direction(StrEnum):
    UP = "up"
    DOWN = "down"


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    STALE = "stale"


class Verdict(StrEnum):
    GOOD_DEAL = "GOOD_DEAL"
    FAIR = "FAIR"
    HIGH = "HIGH"
    SUSPICIOUS = "SUSPICIOUS"
    UNKNOWN = "UNKNOWN"


class AlertKind(StrEnum):
    SHOP_OUTLIER_HIGH = "SHOP_OUTLIER_HIGH"
    SHOP_OUTLIER_LOW = "SHOP_OUTLIER_LOW"
    MARKET_SHIFT = "MARKET_SHIFT"
    DIGEST = "DIGEST"


class Channel(StrEnum):
    INBOX = "inbox"
    EMAIL = "email"
    WEBPUSH = "webpush"
