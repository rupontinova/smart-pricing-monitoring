"""Trust engine (badge D): reputation, report weights, independence, agreement, VERIFIED shop
prices and the resolution of PENDING reports.

Pure functions that return *decisions* (``TrustDelta``, ``PendingResolution``); the scoring worker
(M13) persists them.
"""

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from itertools import combinations
from uuid import UUID

from spm.constants import (
    AGREE_LOG_TOL,
    CORROBORATION_MIN_H,
    DISAGREE_LOG_TOL,
    HIGH_TRUST_MIN_EVIDENCE,
    HIGH_TRUST_MIN_SCORE,
    INDEPENDENCE_MIN_GAP_MIN,
    NO_LOCATION_WEIGHT,
    Z_RESOLVE_NORMAL,
)
from spm.core.enums import Classification
from spm.core.models import Observation
from spm.core.stats import freshness_weight, median, weighted_median

# Observations that may support a shop price (PENDING, BAD_REPORT and EXPIRED may not).
_PRICE_EXCLUDED = {Classification.PENDING, Classification.BAD_REPORT, Classification.EXPIRED}


# ----------------------------------------------------------------------------- reputation
def trust_score(alpha: float, beta: float) -> float:
    """Posterior mean of Beta(α, β)."""
    return alpha / (alpha + beta)


def is_high_trust(alpha: float, beta: float) -> bool:
    """A high score backed by enough evidence (α + β − 2 confirmed outcomes)."""
    return (
        trust_score(alpha, beta) >= HIGH_TRUST_MIN_SCORE
        and alpha + beta - 2 >= HIGH_TRUST_MIN_EVIDENCE
    )


def report_weight(trust: float, age_h: float, half_life_h: int, location_verified: bool) -> float:
    """``trust × freshness × (1 if location verified else NO_LOCATION_WEIGHT)``."""
    location = 1.0 if location_verified else NO_LOCATION_WEIGHT
    return max(trust, 0.0) * freshness_weight(age_h, half_life_h) * location


def corroboration_window_h(half_life_h: int) -> float:
    """How far apart (either way) two observations may be and still corroborate each other."""
    return max(float(CORROBORATION_MIN_H), 0.5 * half_life_h)


# ------------------------------------------------------------------- agreement & independence
def _log_gap(p1: float, p2: float) -> float:
    return abs(math.log(p1 / p2))


def agree(p1: float, p2: float) -> bool:
    return _log_gap(p1, p2) <= AGREE_LOG_TOL


def contradict(p1: float, p2: float) -> bool:
    return _log_gap(p1, p2) > DISAGREE_LOG_TOL


def _gap_h(a: Observation, b: Observation) -> float:
    return abs((a.observed_at - b.observed_at).total_seconds()) / 3600


def independent(a: Observation, b: Observation) -> bool:
    """Different reporters, and not the same network (IP hash) within a few minutes."""
    if a.reporter_id == b.reporter_id:
        return False
    same_network = a.ip_hash is not None and a.ip_hash == b.ip_hash
    return not (same_network and _gap_h(a, b) * 60 < INDEPENDENCE_MIN_GAP_MIN)


def _peers(
    target: Observation, others: Sequence[Observation], window_h: float
) -> list[Observation]:
    """Other reports within ±window_h of ``target`` that are independent of it."""
    return [
        o
        for o in others
        if o.report_id != target.report_id
        and _gap_h(o, target) <= window_h
        and independent(target, o)
    ]


def is_corroborated(
    target: Observation, others: Sequence[Observation], window_h: float, high_trust: bool
) -> tuple[bool, list[UUID]]:
    """Confirmed by an independent agreeing report within the window, or by a high-trust reporter.

    Returns the supporting report ids (possibly empty when only high trust counts).
    """
    support = [
        o.report_id
        for o in _peers(target, others, window_h)
        if agree(target.norm_price, o.norm_price)
    ]
    return high_trust or bool(support), support


# ------------------------------------------------------------------------ trust decisions
@dataclass(frozen=True, slots=True)
class TrustDelta:
    reporter_id: UUID
    d_alpha: float
    d_beta: float
    report_id: UUID  # the report this evidence is about (marked trust_settled by the caller)
    reason: str


@dataclass(frozen=True, slots=True)
class PendingResolution:
    report_id: UUID
    new_classification: Classification  # SHOP_OUTLIER or BAD_REPORT
    resolved_by: UUID
    deltas: tuple[TrustDelta, ...]


def resolve_pending(
    pending: Sequence[Observation],
    new: Observation,
    new_z_market: float | None,
    window_h: float,
) -> list[PendingResolution]:
    """Decide which PENDING reports ``new`` confirms or refutes.

    A pending report within the window and independent of ``new``:
    - agrees with it → SHOP_OUTLIER (the outlier was real), +1 α to its reporter;
    - contradicts it while ``new`` agrees with the market (|z_market| ≤ Z_RESOLVE_NORMAL)
      → BAD_REPORT, +1 β to its reporter;
    - otherwise stays PENDING.
    """
    resolutions: list[PendingResolution] = []
    for p in _peers(new, pending, window_h):
        if agree(p.norm_price, new.norm_price):
            outcome, delta = (
                Classification.SHOP_OUTLIER,
                TrustDelta(p.reporter_id, 1.0, 0.0, p.report_id, "pending_confirmed"),
            )
        elif (
            contradict(p.norm_price, new.norm_price)
            and new_z_market is not None
            and abs(new_z_market) <= Z_RESOLVE_NORMAL
        ):
            outcome, delta = (
                Classification.BAD_REPORT,
                TrustDelta(p.reporter_id, 0.0, 1.0, p.report_id, "pending_contradicted"),
            )
        else:
            continue
        resolutions.append(PendingResolution(p.report_id, outcome, new.report_id, (delta,)))
    return resolutions


def settle_normal(
    new: Observation, peers: Sequence[Observation], window_h: float
) -> list[TrustDelta]:
    """Ongoing reputation for an ordinary report, against peers not yet settled.

    - At least one independent peer in the window agrees → +1 α to ``new``'s reporter and to
      each agreeing peer's reporter.
    - Otherwise, if two independent peers agree with each other and both contradict ``new``
      → +1 β to ``new``'s reporter.
    Agreement wins: a corroborated report is never penalized.
    """
    nearby = _peers(new, peers, window_h)
    agreeing = [p for p in nearby if agree(p.norm_price, new.norm_price)]
    if agreeing:
        return [TrustDelta(new.reporter_id, 1.0, 0.0, new.report_id, "agreed")] + [
            TrustDelta(p.reporter_id, 1.0, 0.0, p.report_id, "agreed") for p in agreeing
        ]
    contradicting = [p for p in nearby if contradict(p.norm_price, new.norm_price)]
    for a, b in combinations(contradicting, 2):
        if independent(a, b) and agree(a.norm_price, b.norm_price):
            return [TrustDelta(new.reporter_id, 0.0, 1.0, new.report_id, "contradicted")]
    return []


# ---------------------------------------------------------------------- verified shop price
def verified_shop_price(
    obs: Sequence[Observation],
    now: datetime,
    half_life_h: int,
    high_trust: Callable[[UUID], bool],
) -> tuple[bool, float | None, list[UUID]]:
    """A shop's current price for one product: (verified, price, supporting report ids).

    Uses observations not PENDING / BAD_REPORT / EXPIRED and at most 2 half-lives old. The
    cluster is every observation agreeing with the most recent one; it is VERIFIED with ≥ 2
    independent observations or one high-trust reporter. Price = weighted median of the cluster.
    """
    max_age_h = 2 * half_life_h

    def age_h(o: Observation) -> float:
        return max((now - o.observed_at).total_seconds() / 3600, 0.0)

    usable = [o for o in obs if o.classification not in _PRICE_EXCLUDED and age_h(o) <= max_age_h]
    if not usable:
        return False, None, []
    latest = max(usable, key=lambda o: (o.observed_at, o.report_id))
    cluster = [o for o in usable if agree(o.norm_price, latest.norm_price)]

    verified = any(high_trust(o.reporter_id) for o in cluster) or any(
        independent(a, b) for a, b in combinations(cluster, 2)
    )
    prices = [o.norm_price for o in cluster]
    weights = [report_weight(o.trust, age_h(o), half_life_h, o.location_verified) for o in cluster]
    price = weighted_median(prices, weights) if sum(weights) > 0 else median(prices)
    return verified, price, [o.report_id for o in cluster]
