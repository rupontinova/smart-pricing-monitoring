import uuid
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st

from spm.core.enums import Classification
from spm.core.models import Observation
from spm.core.trust import (
    agree,
    contradict,
    corroboration_window_h,
    independent,
    is_corroborated,
    is_high_trust,
    report_weight,
    resolve_pending,
    settle_normal,
    trust_score,
    verified_shop_price,
)

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
WINDOW_H = 24.0


def obs(
    price: float,
    *,
    minutes_ago: float = 0,
    reporter: UUID | None = None,
    ip: str | None = None,
    classification: Classification | None = None,
    trust: float = 0.5,
    verified_location: bool = True,
) -> Observation:
    return Observation(
        report_id=uuid.uuid4(),
        reporter_id=reporter or uuid.uuid4(),
        shop_id="shop-a",
        norm_price=price,
        observed_at=NOW - timedelta(minutes=minutes_ago),
        trust=trust,
        location_verified=verified_location,
        classification=classification,
        ip_hash=ip,
    )


# ---------------------------------------------------------------------------- reputation
def test_trust_score_and_high_trust() -> None:
    assert trust_score(1, 1) == 0.5
    assert not is_high_trust(9, 1)  # score 0.9 but only 8 pieces of evidence
    assert is_high_trust(11, 2)  # 0.846, evidence 11
    assert not is_high_trust(20, 10)  # plenty of evidence, score 0.667


def test_report_weight_location_and_age() -> None:
    full = report_weight(0.8, 0, 48, True)
    assert full == pytest.approx(0.8)
    assert report_weight(0.8, 0, 48, False) == pytest.approx(full / 2)
    assert report_weight(0.8, 48, 48, False) == pytest.approx(full / 4)


def test_corroboration_window() -> None:
    assert corroboration_window_h(48) == 24
    assert corroboration_window_h(8) == 6  # floor


# -------------------------------------------------------------------- agreement & independence
def test_agree_and_contradict() -> None:
    assert agree(80, 83.9)  # ln ≈ 0.048
    assert not agree(80, 85)  # ln ≈ 0.061
    assert contradict(80, 90)  # ln ≈ 0.118
    assert not contradict(80, 85)  # between the two tolerances: neither
    assert agree(83.9, 80)  # symmetric


def test_independence() -> None:
    reporter = uuid.uuid4()
    assert not independent(obs(80, reporter=reporter), obs(80, reporter=reporter, minutes_ago=600))
    assert not independent(obs(80, ip="h1"), obs(80, ip="h1", minutes_ago=5))
    assert independent(obs(80, ip="h1"), obs(80, ip="h1", minutes_ago=15))
    assert independent(obs(80, ip="h1"), obs(80, ip="h2", minutes_ago=1))
    assert independent(obs(80), obs(80, minutes_ago=1))  # no ip hashes


def test_is_corroborated() -> None:
    target = obs(160)
    support = obs(158, minutes_ago=60)
    far_away = obs(159, minutes_ago=WINDOW_H * 60 + 1)
    disagree = obs(120, minutes_ago=30)
    ok, ids = is_corroborated(target, [target, support, far_away, disagree], WINDOW_H, False)
    assert ok and ids == [support.report_id]
    assert is_corroborated(target, [disagree], WINDOW_H, False) == (False, [])
    assert is_corroborated(target, [], WINDOW_H, True) == (True, [])


# ------------------------------------------------------------------------ resolve_pending
def test_pending_confirmed_by_independent_agreeing_report() -> None:
    pending = obs(160, minutes_ago=120, classification=Classification.PENDING)
    new = obs(158)
    (res,) = resolve_pending([pending], new, new_z_market=4.0, window_h=WINDOW_H)
    assert res.report_id == pending.report_id
    assert res.new_classification is Classification.SHOP_OUTLIER
    assert res.resolved_by == new.report_id
    (delta,) = res.deltas
    assert (delta.reporter_id, delta.d_alpha, delta.d_beta) == (pending.reporter_id, 1.0, 0.0)


def test_pending_contradicted_by_market_conforming_report() -> None:
    pending = obs(160, minutes_ago=120, classification=Classification.PENDING)
    (res,) = resolve_pending([pending], obs(81), new_z_market=0.3, window_h=WINDOW_H)
    assert res.new_classification is Classification.BAD_REPORT
    assert (res.deltas[0].d_alpha, res.deltas[0].d_beta) == (0.0, 1.0)


@pytest.mark.parametrize(
    ("z_market", "new_price"),
    [(None, 81), (2.5, 81), (0.3, 150)],  # unknown market / new isn't normal / gap in between
)
def test_pending_unchanged(z_market: float | None, new_price: float) -> None:
    pending = obs(160, minutes_ago=120, classification=Classification.PENDING)
    assert resolve_pending([pending], obs(new_price), z_market, WINDOW_H) == []


def test_pending_same_reporter_or_outside_window_not_resolved() -> None:
    reporter = uuid.uuid4()
    pending = obs(160, minutes_ago=120, reporter=reporter, classification=Classification.PENDING)
    assert resolve_pending([pending], obs(158, reporter=reporter), 0.3, WINDOW_H) == []
    old = obs(160, minutes_ago=WINDOW_H * 60 + 5, classification=Classification.PENDING)
    assert resolve_pending([old], obs(158), 0.3, WINDOW_H) == []


# -------------------------------------------------------------------------- settle_normal
def test_settle_normal_rewards_agreeing_pair() -> None:
    new, peer = obs(80), obs(81, minutes_ago=30)
    deltas = settle_normal(new, [peer], WINDOW_H)
    assert {(d.reporter_id, d.d_alpha, d.d_beta) for d in deltas} == {
        (new.reporter_id, 1.0, 0.0),
        (peer.reporter_id, 1.0, 0.0),
    }
    assert {d.report_id for d in deltas} == {new.report_id, peer.report_id}


def test_settle_normal_penalizes_report_contradicted_by_two_agreeing_peers() -> None:
    new = obs(120)
    a, b = obs(80, minutes_ago=30, ip="x"), obs(81, minutes_ago=60, ip="y")
    (delta,) = settle_normal(new, [a, b], WINDOW_H)
    assert (delta.reporter_id, delta.d_alpha, delta.d_beta) == (new.reporter_id, 0.0, 1.0)


def test_settle_normal_needs_two_independent_contradicting_peers() -> None:
    new = obs(120)
    same = uuid.uuid4()
    a, b = obs(80, minutes_ago=30, reporter=same), obs(81, minutes_ago=60, reporter=same)
    assert settle_normal(new, [a, b], WINDOW_H) == []  # one reporter twice
    assert settle_normal(new, [obs(80, minutes_ago=30)], WINDOW_H) == []  # one peer
    assert settle_normal(new, [obs(80, minutes_ago=30), obs(95, minutes_ago=40)], WINDOW_H) == []


def test_settle_normal_agreement_wins_over_contradiction() -> None:
    new = obs(120)
    deltas = settle_normal(
        new, [obs(121), obs(80, minutes_ago=30), obs(81, minutes_ago=60)], WINDOW_H
    )
    assert all(d.d_beta == 0 for d in deltas)
    assert len(deltas) == 2


def test_settle_normal_ignores_peers_outside_window() -> None:
    assert settle_normal(obs(80), [obs(80, minutes_ago=WINDOW_H * 60 + 1)], WINDOW_H) == []


# --------------------------------------------------------------------- verified_shop_price
def never(_: UUID) -> bool:
    return False


def test_single_report_not_verified() -> None:
    o = obs(80)
    assert verified_shop_price([o], NOW, 48, never) == (False, 80.0, [o.report_id])


def test_two_agreeing_independent_reports_verified_at_weighted_median() -> None:
    a = obs(80, minutes_ago=10, trust=0.9)
    b = obs(82, minutes_ago=20, trust=0.3)
    verified, price, ids = verified_shop_price([a, b], NOW, 48, never)
    assert verified and set(ids) == {a.report_id, b.report_id}
    assert price is not None and 80 <= price < 81  # pulled toward the more trusted 80


def test_two_reports_same_reporter_not_verified() -> None:
    r = uuid.uuid4()
    verified, _, _ = verified_shop_price(
        [obs(80, reporter=r), obs(80, minutes_ago=30, reporter=r)], NOW, 48, never
    )
    assert not verified


def test_one_high_trust_report_verified() -> None:
    o = obs(80)
    verified, price, _ = verified_shop_price([o], NOW, 48, lambda rid: rid == o.reporter_id)
    assert verified and price == 80


def test_cluster_starts_from_most_recent_and_excludes_bad_and_stale() -> None:
    latest = obs(100, minutes_ago=5)
    agreeing = obs(102, minutes_ago=50)
    old_price = obs(80, minutes_ago=100)  # disagrees with the latest: not in the cluster
    bad = obs(101, minutes_ago=10, classification=Classification.BAD_REPORT)
    pending = obs(101, minutes_ago=10, classification=Classification.PENDING)
    stale = obs(100, minutes_ago=97 * 60)  # older than 2 × 48 h
    verified, price, ids = verified_shop_price(
        [latest, agreeing, old_price, bad, pending, stale], NOW, 48, never
    )
    assert set(ids) == {latest.report_id, agreeing.report_id}
    assert verified and price is not None and 100 <= price <= 102


def test_no_usable_observations() -> None:
    stale = obs(80, minutes_ago=200 * 60)
    assert verified_shop_price([stale], NOW, 48, never) == (False, None, [])


def test_zero_trust_falls_back_to_plain_median() -> None:
    a, b = obs(80, trust=0.0), obs(82, minutes_ago=1, trust=0.0)
    assert verified_shop_price([a, b], NOW, 48, never)[1] == 81.0


# ----------------------------------------------------------------------------- properties
evidence = st.floats(min_value=1e-6, max_value=1e6, allow_nan=False)


@given(evidence, evidence)
def test_trust_in_open_unit_interval(alpha: float, beta: float) -> None:
    # α/β ≤ 1e12 here, so 1 − score ≥ ~1e-12: representable, strictly inside (0, 1).
    assert 0.0 < trust_score(alpha, beta) < 1.0


@given(
    st.floats(min_value=-1.0, max_value=1.0),
    st.floats(min_value=-1e4, max_value=1e6, allow_nan=False),
    st.integers(min_value=1, max_value=504),
    st.booleans(),
)
def test_weights_never_negative(trust: float, age_h: float, half_life_h: int, loc: bool) -> None:
    assert report_weight(trust, age_h, half_life_h, loc) >= 0.0
