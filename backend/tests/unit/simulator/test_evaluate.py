"""The evaluator on a hand-built run, so every number can be checked by hand (no Docker)."""

import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from simulator.evaluate import evaluate, to_markdown, write_report
from simulator.generator import Market, SimReport, SimStream, Truth, event_windows
from simulator.runner import AnomalyRow, BandSnapshot, Outcome, Prediction, RunResult
from simulator.scenario import Scenario, Targets
from spm.core.enums import Classification as C
from spm.core.enums import ScoringState

TZ = ZoneInfo("Asia/Dhaka")
START = datetime(2026, 1, 5, tzinfo=TZ)  # day 0, local midnight
GOUGE_START = datetime(2026, 1, 10, 8, tzinfo=TZ)  # day 5, when the shop opens


def _scenario(targets: Targets) -> Scenario:
    return Scenario.model_validate(
        {
            "name": "hand",
            "days": 10,
            "products": ["eggs"],
            "true_price": {"eggs": 150},
            "shop_premium": {"shop-a": 0, "shop-b": 0, "shop-c": 0},
            "events": [
                {
                    "type": "shop_gouging",
                    "day": 5,
                    "product": "eggs",
                    "shop": "shop-c",
                    "multiplier": 1.3,
                    "duration_days": 2,
                }
            ],
            "targets": targets,
        }
    )


def _report(seq: int, truth: Truth, shop: str = "shop-a", hours: float = 1.0) -> SimReport:
    t = START + timedelta(hours=hours)
    return SimReport(
        seq,
        f"dev-{seq}",
        "honest",
        f"ip-{seq}",
        uuid.uuid4(),
        "eggs",
        shop,
        Decimal(150),
        Decimal(1),
        "dozen",
        t,
        t + timedelta(minutes=5),
        None,
        150.0,
        truth,
    )


def _run(targets: Targets | None = None) -> tuple[RunResult, list[uuid.UUID]]:
    s = _scenario(targets or Targets())
    # truth -> what the system said
    plan: list[tuple[Truth, C | str]] = [
        (Truth.NORMAL, C.NORMAL),
        (Truth.NORMAL, C.NORMAL),
        (Truth.NORMAL, C.NORMAL),
        (Truth.NORMAL, C.PENDING),
        (Truth.BAD_UNIT, C.BAD_REPORT),
        (Truth.BAD_UNIT, C.SHOP_OUTLIER),
        (Truth.BAD_UNIT, "REJECTED"),
        (Truth.SHOP_OUTLIER, C.SHOP_OUTLIER),
        (Truth.NORMAL, "DUPLICATE"),
        (Truth.BAD_UNIT, C.PENDING),  # reported 2 h before the end: still OPEN
    ]
    reports, outcomes, ids = [], [], []
    predictions: dict[uuid.UUID, Prediction] = {}
    for i, (truth, said) in enumerate(plan):
        reports.append(_report(i, truth, hours=10 * 24 - 2 if i == len(plan) - 1 else 1.0))
        rid = uuid.uuid4()
        ids.append(rid)
        if said == "REJECTED":
            outcomes.append(Outcome(i, None, rejected="PRICE_OUT_OF_BOUNDS"))
        elif said == "DUPLICATE":
            outcomes.append(Outcome(i, rid, duplicate=True))
        else:
            outcomes.append(Outcome(i, rid))
            predictions[rid] = Prediction(ScoringState.SCORED, C(said))
    market = Market(s, TZ)
    stream = SimStream(s, reports, event_windows(s, market), market, {"eggs": 120})

    def anomaly(shop: str, hours: float, report: int) -> AnomalyRow:
        return AnomalyRow(
            uuid.uuid4(),
            "SHOP_OUTLIER",
            "up",
            "eggs",
            shop,
            GOUGE_START + timedelta(hours=hours),
            (ids[report],),
            30.0,
        )

    anomalies = [
        anomaly("shop-c", 3, 7),  # the gouging, detected 3 h in
        anomaly("shop-c", 20, 7),  # a repeat alert for the same event
        anomaly("shop-a", 4, 5),  # built on a bad-unit report: a false alarm
        anomaly("shop-b", -48, 3),  # before the event: noise
    ]
    fair = 150.0
    bands = [
        BandSnapshot(0, START + timedelta(days=1), "eggs", 200.0, "low", 5.0, None, fair),
        BandSnapshot(3, START + timedelta(days=4), "eggs", 153.0, "high", 10.0, None, fair),
        BandSnapshot(
            4,
            START + timedelta(days=5),
            "eggs",
            144.0,
            "high",
            20.0,
            START + timedelta(days=4, hours=18),
            fair,
        ),
    ]
    run = RunResult(stream, "inprocess", outcomes, predictions, anomalies, bands, 2.0, 0)
    return run, ids


def test_confusion_matrix_and_class_scores() -> None:
    ev = evaluate(_run()[0])
    normal, bad = ev.confusion["normal"], ev.confusion["bad_unit"]
    assert (normal["NORMAL"], normal["PENDING"], normal["DUPLICATE"]) == (3, 1, 1)
    assert (bad["BAD_REPORT"], bad["SHOP_OUTLIER"], bad["REJECTED"]) == (1, 1, 1)
    assert (bad["OPEN"], bad["PENDING"]) == (1, 0)
    assert ev.confusion["shop_outlier"]["SHOP_OUTLIER"] == 1
    scores = {s.label: s for s in ev.scores}
    assert (scores["NORMAL"].precision, scores["NORMAL"].recall) == (1.0, 0.75)
    assert (scores["SHOP_OUTLIER"].precision, scores["SHOP_OUTLIER"].recall) == (0.5, 1.0)
    assert scores["MARKET_SHIFT"].precision is None
    # Rejected and still-open bad-unit reports are not in the denominator: 1 of 2.
    assert ev.bad_unit_recall == 0.5
    assert (ev.accepted, ev.duplicates, ev.rejected) == (8, 1, 1)
    assert ev.rejections == {"PRICE_OUT_OF_BOUNDS": 1}


def test_event_detection_and_false_alarms() -> None:
    ev = evaluate(_run()[0])
    (event,) = ev.events
    assert event.detected and event.time_to_detect_h == pytest.approx(3.0)
    assert (event.expected_kind_alerts, event.alerts) == (2, 2)
    causes = sorted((f.shop, f.cause) for f in ev.false_alarms)
    assert causes == [("shop-a", "bad_data"), ("shop-b", "noise")]
    assert ev.false_outliers_per_product_per_30d == {"eggs": 2 * 30 / 10}


def test_band_accuracy_skips_the_warmup_days() -> None:
    ev = evaluate(_run()[0])  # warm-up 3 days: the day-0 snapshot (p50 200) is ignored
    assert ev.band_mape == pytest.approx((3 / 150 + 6 / 150) / 2)
    assert ev.band_coverage == 1.0
    assert ev.freshness["eggs"]["median_band_mean_age_h"] == 10.0
    assert ev.freshness["eggs"]["median_newest_report_age_h"] == pytest.approx(6.0)


def test_targets_pass_and_fail() -> None:
    ev = evaluate(
        _run(
            Targets(
                bad_unit_recall_min=0.9,
                outlier_detect_within_h=12,
                no_outlier_on_other_shops=True,
                max_false_outliers_per_product_per_30d=6,
                band_mape_max=0.05,
            )
        )[0]
    )
    results = {t.name: t.passed for t in ev.targets}
    assert results == {
        "bad_unit_recall": False,  # 0.5
        "outlier_time_to_detect_h": True,  # 3 h
        "outliers_on_other_shops": False,  # shop-a during the gouging
        "false_outliers_per_product_per_30d": True,  # 6.0
        "band_mape": True,  # 3%
    }
    assert not ev.passed


def test_report_files(tmp_path: Path) -> None:
    ev = evaluate(_run(Targets(band_mape_max=0.05))[0])
    at = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
    md = write_report(ev, at, tmp_path)
    assert md.name == "hand-20261024T120000.md"
    text = md.read_text(encoding="utf-8")
    assert "## Confusion matrix" in text and "✅ pass" in text
    assert text == to_markdown(ev, at)
    data = json.loads(md.with_suffix(".json").read_text(encoding="utf-8"))
    assert data["passed"] is True
    assert data["confusion"]["bad_unit"]["REJECTED"] == 1
    assert {s["label"]: s["recall"] for s in data["scores"]}["NORMAL"] == 0.75
