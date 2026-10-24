"""Join what the system concluded with the ground truth, and write the evaluation report."""

import json
import statistics
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from simulator.generator import EventWindow, Truth
from simulator.runner import AnomalyRow, RunResult
from simulator.scenario import MarketShock, ShopGouging
from spm.core.enums import AnomalyKind, Classification
from spm.core.trust import corroboration_window_h

OUT_DIR = Path(__file__).parent / "out"

# An anomaly belongs to a true event if it appears between the event's start and this long after
# the event's end (a shock is "fully in place" at the end of its ramp; detection may lag).
MATCH_GRACE = timedelta(hours=48)

# OPEN: still PENDING with its corroboration window running past the end of the run, so it has
# no verdict yet; like REJECTED and DUPLICATE it is outside the precision/recall denominators.
REJECTED, DUPLICATE, UNSCORED, OPEN = "REJECTED", "DUPLICATE", "UNSCORED", "OPEN"
COLUMNS = [c.value for c in Classification] + [OPEN, REJECTED, DUPLICATE, UNSCORED]

# The classification each truth should receive.
EXPECTED: dict[Truth, Classification] = {
    Truth.NORMAL: Classification.NORMAL,
    Truth.BAD_UNIT: Classification.BAD_REPORT,
    Truth.MALICIOUS: Classification.BAD_REPORT,
    Truth.SHOP_OUTLIER: Classification.SHOP_OUTLIER,
    Truth.MARKET_SHIFT: Classification.MARKET_SHIFT,
}


@dataclass(frozen=True, slots=True)
class ClassScore:
    label: str
    predicted: int
    actual: int  # scored reports whose truth expects this label
    correct: int

    @property
    def precision(self) -> float | None:
        return self.correct / self.predicted if self.predicted else None

    @property
    def recall(self) -> float | None:
        return self.correct / self.actual if self.actual else None


@dataclass(frozen=True, slots=True)
class EventResult:
    label: str
    expected_kind: str
    product: str
    shop: str | None
    start: datetime
    detected: bool
    time_to_detect_h: float | None
    alerts: int  # every anomaly attributed to the event, of any kind
    expected_kind_alerts: int


@dataclass(frozen=True, slots=True)
class FalseAlarm:
    kind: str
    product: str
    shop: str | None
    direction: str | None
    detected_at: datetime
    cause: str  # bad_data (built on malicious / bad-unit reports) or noise


@dataclass(frozen=True, slots=True)
class TargetResult:
    name: str
    description: str
    value: float | None
    threshold: float
    passed: bool


@dataclass(slots=True)
class Evaluation:
    scenario: str
    mode: str
    seed: int
    days: int
    generated: int
    accepted: int
    duplicates: int
    rejected: int
    elapsed_s: float
    dead_letters: int
    truth_counts: dict[str, int]
    confusion: dict[str, dict[str, int]]
    scores: list[ClassScore]
    bad_unit_recall: float | None
    events: list[EventResult]
    false_alarms: list[FalseAlarm]
    false_outliers_per_product_per_30d: dict[str, float]
    band_mape: float | None
    band_mape_by_product: dict[str, float | None]
    band_coverage: float | None
    band_warmup_days: int
    freshness: dict[str, dict[str, float | None]]
    rejections: dict[str, int]
    anomalies_by_kind: dict[str, int]
    targets: list[TargetResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(t.passed for t in self.targets)

    def target(self, name: str) -> TargetResult:
        return next(t for t in self.targets if t.name == name)


# ------------------------------------------------------------------------------- pieces
def _matches(a: AnomalyRow, e: EventWindow) -> bool:
    return (
        a.product_id == e.product
        and a.direction == e.direction.value
        and (e.shop is None or a.shop_id == e.shop)
        and e.start <= a.detected_at <= e.end + MATCH_GRACE
    )


def _event_results(run: RunResult) -> tuple[list[EventResult], list[AnomalyRow]]:
    results: list[EventResult] = []
    matched: set[UUID] = set()
    for e in run.stream.events:
        mine = [a for a in run.anomalies if _matches(a, e)]
        matched.update(a.id for a in mine)
        expected = [a for a in mine if a.kind == e.expected_kind.value]
        first = min((a.detected_at for a in expected), default=None)
        results.append(
            EventResult(
                e.label,
                e.expected_kind.value,
                e.product,
                e.shop,
                e.start,
                first is not None,
                (first - e.start).total_seconds() / 3600 if first else None,
                len(mine),
                len(expected),
            )
        )
    return results, [a for a in run.anomalies if a.id not in matched]


def _false_alarms(run: RunResult, unmatched: list[AnomalyRow]) -> list[FalseAlarm]:
    truth_by_id = {
        o.report_id: run.stream.reports[o.seq].truth for o in run.outcomes if o.report_id
    }
    bad = {Truth.MALICIOUS, Truth.BAD_UNIT}
    return [
        FalseAlarm(
            a.kind,
            a.product_id,
            a.shop_id,
            a.direction,
            a.detected_at,
            "bad_data" if any(truth_by_id.get(r) in bad for r in a.report_ids) else "noise",
        )
        for a in unmatched
    ]


def _still_open(run: RunResult, seq: int) -> bool:
    r = run.stream.reports[seq]
    window = timedelta(hours=corroboration_window_h(run.stream.half_lives[r.product]))
    return r.observed_at + window > run.stream.end


def _confusion(run: RunResult) -> tuple[dict[str, dict[str, int]], Counter[str]]:
    confusion = {t.value: dict.fromkeys(COLUMNS, 0) for t in Truth}
    rejections: Counter[str] = Counter()
    for o in run.outcomes:
        truth = run.stream.reports[o.seq].truth.value
        if o.rejected:
            column = REJECTED
            rejections[o.rejected] += 1
        elif o.duplicate:
            column = DUPLICATE
        else:
            p = run.predictions.get(o.report_id) if o.report_id else None
            column = p.classification.value if p and p.classification else UNSCORED
            if column == Classification.PENDING.value and _still_open(run, o.seq):
                column = OPEN
        confusion[truth][column] += 1
    return confusion, rejections


def _scores(confusion: dict[str, dict[str, int]]) -> list[ClassScore]:
    scored = [c.value for c in Classification]
    scores = []
    for label in (
        Classification.NORMAL,
        Classification.BAD_REPORT,
        Classification.SHOP_OUTLIER,
        Classification.MARKET_SHIFT,
    ):
        truths = [t.value for t, c in EXPECTED.items() if c is label]
        predicted = sum(row[label.value] for row in confusion.values())
        actual = sum(confusion[t][c] for t in truths for c in scored)
        correct = sum(confusion[t][label.value] for t in truths)
        scores.append(ClassScore(label.value, predicted, actual, correct))
    return scores


def _band(
    run: RunResult, warmup: int
) -> tuple[float | None, dict[str, float | None], float | None]:
    errors: dict[str, list[float]] = {p: [] for p in run.stream.scenario.products}
    eligible = 0
    for snap in run.bands:
        if snap.day < warmup:
            continue
        eligible += 1
        if snap.p50 is not None:
            errors[snap.product].append(abs(snap.p50 - snap.fair_price) / snap.fair_price)
    all_errors = [e for errs in errors.values() for e in errs]
    by_product = {p: statistics.fmean(e) if e else None for p, e in errors.items()}
    coverage = len(all_errors) / eligible if eligible else None
    return (statistics.fmean(all_errors) if all_errors else None), by_product, coverage


def _freshness(run: RunResult) -> dict[str, dict[str, float | None]]:
    out: dict[str, dict[str, float | None]] = {}
    for product in run.stream.scenario.products:
        snaps = [s for s in run.bands if s.product == product and s.p50 is not None]
        ages = [s.mean_age_h for s in snaps if s.mean_age_h is not None]
        newest = [(s.at - s.newest_obs_at).total_seconds() / 3600 for s in snaps if s.newest_obs_at]
        out[product] = {
            "median_band_mean_age_h": statistics.median(ages) if ages else None,
            "median_newest_report_age_h": statistics.median(newest) if newest else None,
        }
    return out


def _targets(ev: Evaluation, run: RunResult) -> list[TargetResult]:
    t = run.stream.scenario.targets
    events = run.stream.scenario.events
    out: list[TargetResult] = []

    def add(name: str, description: str, value: float | None, threshold: float, ok: bool) -> None:
        out.append(TargetResult(name, description, value, threshold, ok))

    if t.bad_unit_recall_min is not None:
        v = ev.bad_unit_recall
        add(
            "bad_unit_recall",
            "BAD_REPORT recall on scored bad_unit reports ≥",
            v,
            t.bad_unit_recall_min,
            v is not None and v >= t.bad_unit_recall_min,
        )

    def worst_ttd(kind: type) -> float | None:
        picked = [r for r, e in zip(ev.events, events, strict=True) if isinstance(e, kind)]
        if not picked or any(r.time_to_detect_h is None for r in picked):
            return None
        return max(r.time_to_detect_h for r in picked if r.time_to_detect_h is not None)

    if t.shift_detect_within_h is not None:
        v = worst_ttd(MarketShock)
        add(
            "shift_time_to_detect_h",
            "MARKET_SHIFT detected within (h of the shock start)",
            v,
            t.shift_detect_within_h,
            v is not None and v <= t.shift_detect_within_h,
        )
    if t.max_shift_anomalies_per_direction is not None:
        # Repeated alerts for the same shock; unrelated shifts are counted as false alarms.
        per_shock = [
            r.expected_kind_alerts
            for r, e in zip(ev.events, events, strict=True)
            if isinstance(e, MarketShock)
        ]
        v = float(max(per_shock, default=0))
        limit = float(t.max_shift_anomalies_per_direction)
        add(
            "shift_anomalies_per_shock",
            "MARKET_SHIFT anomalies per shock (product, direction) ≤",
            v,
            limit,
            v <= limit,
        )
    if t.outlier_detect_within_h is not None:
        v = worst_ttd(ShopGouging)
        add(
            "outlier_time_to_detect_h",
            "SHOP_OUTLIER on the gouging shop within (h)",
            v,
            t.outlier_detect_within_h,
            v is not None and v <= t.outlier_detect_within_h,
        )
    if t.no_outlier_on_other_shops:
        others = 0
        for e in run.stream.events:
            if e.shop is None:
                continue
            others += sum(
                a.kind == AnomalyKind.SHOP_OUTLIER.value
                and a.product_id == e.product
                and a.shop_id != e.shop
                and e.start <= a.detected_at <= e.end + MATCH_GRACE
                for a in run.anomalies
            )
        add(
            "outliers_on_other_shops",
            "SHOP_OUTLIER anomalies on other shops during gouging ≤",
            float(others),
            0,
            others == 0,
        )
    if t.max_false_outliers_per_product_per_30d is not None:
        v = max(ev.false_outliers_per_product_per_30d.values(), default=0.0)
        limit = t.max_false_outliers_per_product_per_30d
        add(
            "false_outliers_per_product_per_30d",
            "false SHOP_OUTLIER per product per 30 days ≤",
            v,
            limit,
            v <= limit,
        )
    if t.band_mape_max is not None:
        v = ev.band_mape
        add(
            "band_mape",
            f"band p50 MAPE after day {t.band_warmup_days} ≤",
            v,
            t.band_mape_max,
            v is not None and v <= t.band_mape_max,
        )
    return out


def evaluate(run: RunResult) -> Evaluation:
    s = run.stream.scenario
    confusion, rejections = _confusion(run)
    scores = _scores(confusion)
    bad_row = confusion[Truth.BAD_UNIT.value]
    bad_scored = sum(bad_row[c.value] for c in Classification)
    events, unmatched = _event_results(run)
    false_alarms = _false_alarms(run, unmatched)
    false_outliers = Counter(
        f.product for f in false_alarms if f.kind == AnomalyKind.SHOP_OUTLIER.value
    )
    mape, mape_by_product, coverage = _band(run, s.targets.band_warmup_days)
    ev = Evaluation(
        scenario=s.name,
        mode=run.mode,
        seed=s.seed,
        days=s.days,
        generated=len(run.stream.reports),
        accepted=sum(1 for o in run.outcomes if o.report_id and not o.duplicate),
        duplicates=sum(1 for o in run.outcomes if o.duplicate),
        rejected=sum(1 for o in run.outcomes if o.rejected),
        elapsed_s=round(run.elapsed_s, 2),
        dead_letters=run.dead_letters,
        truth_counts=dict(Counter(r.truth.value for r in run.stream.reports)),
        confusion=confusion,
        scores=scores,
        bad_unit_recall=bad_row[Classification.BAD_REPORT.value] / bad_scored
        if bad_scored
        else None,
        events=events,
        false_alarms=false_alarms,
        false_outliers_per_product_per_30d={p: false_outliers[p] * 30 / s.days for p in s.products},
        band_mape=mape,
        band_mape_by_product=mape_by_product,
        band_coverage=coverage,
        band_warmup_days=s.targets.band_warmup_days,
        freshness=_freshness(run),
        rejections=dict(rejections.most_common()),
        anomalies_by_kind=dict(Counter(a.kind for a in run.anomalies)),
    )
    ev.targets = _targets(ev, run)
    return ev


# ------------------------------------------------------------------------------- output
def to_json(ev: Evaluation) -> dict[str, Any]:
    data = asdict(ev)
    data["scores"] = [
        {**asdict(s), "precision": s.precision, "recall": s.recall} for s in ev.scores
    ]
    data["passed"] = ev.passed
    return data


def _pct(x: float | None, digits: int = 1) -> str:
    return "—" if x is None else f"{x * 100:.{digits}f}%"


def _num(x: float | None, digits: int = 1) -> str:
    return "—" if x is None else f"{x:.{digits}f}"


def to_markdown(ev: Evaluation, generated_at: datetime) -> str:
    lines = [
        f"# Simulation report: `{ev.scenario}`",
        "",
        f"Mode `{ev.mode}` · seed {ev.seed} · {ev.days} days · generated "
        f"{generated_at:%Y-%m-%d %H:%M} UTC",
        "",
        f"**{ev.generated}** reports generated: {ev.accepted} accepted, {ev.duplicates} "
        f"duplicates, {ev.rejected} rejected. Replay took {ev.elapsed_s:.1f} s "
        f"({ev.generated / ev.elapsed_s if ev.elapsed_s else 0:.0f} reports/s); "
        f"{ev.dead_letters} dead letters.",
        "",
    ]
    if ev.targets:
        lines += ["## Targets", "", "| Target | Value | Threshold | Result |", "|---|---|---|---|"]
        for t in ev.targets:
            value = _num(t.value, 3) if t.value is not None else "not reached"
            lines.append(
                f"| {t.description} | {value} | {t.threshold:g} | "
                f"{'✅ pass' if t.passed else '❌ fail'} |"
            )
        lines.append("")

    lines += [
        "## Confusion matrix (truth × classification)",
        "",
        "| truth | " + " | ".join(COLUMNS) + " | total |",
        "|---|" + "---|" * (len(COLUMNS) + 1),
    ]
    for truth, row in ev.confusion.items():
        total = sum(row.values())
        if total:
            lines.append(
                f"| {truth} | " + " | ".join(str(row[c]) for c in COLUMNS) + f" | {total} |"
            )
    lines += [
        "",
        "| class | predicted | expected | correct | precision | recall |",
        "|---|---|---|---|---|---|",
    ]
    for s in ev.scores:
        lines.append(
            f"| {s.label} | {s.predicted} | {s.actual} | {s.correct} | {_pct(s.precision)} | "
            f"{_pct(s.recall)} |"
        )
    lines += [
        "",
        f"BAD_REPORT recall on scored `bad_unit` reports: **{_pct(ev.bad_unit_recall)}** "
        "(REJECTED by the guardrails and still-OPEN reports are outside the denominator).",
        "",
        "## True events",
        "",
    ]
    if ev.events:
        lines += [
            "| event | expected | detected | time to detect | alerts (expected kind / all) |",
            "|---|---|---|---|---|",
        ]
        for e in ev.events:
            lines.append(
                f"| {e.label} | {e.expected_kind} | {'yes' if e.detected else 'no'} | "
                f"{_num(e.time_to_detect_h)} h | {e.expected_kind_alerts} / {e.alerts} |"
            )
    else:
        lines.append("No true events in this scenario.")
    lines += ["", "## False alarms", ""]
    if ev.false_alarms:
        lines += [
            "| detected at | kind | product | shop | direction | cause |",
            "|---|---|---|---|---|---|",
        ]
        for f in ev.false_alarms:
            lines.append(
                f"| {f.detected_at:%Y-%m-%d %H:%M} | {f.kind} | {f.product} | {f.shop or '*'} | "
                f"{f.direction or '—'} | {f.cause} |"
            )
    else:
        lines.append("None.")
    per30 = ", ".join(f"{p} {v:.2f}" for p, v in ev.false_outliers_per_product_per_30d.items())
    lines += [
        "",
        f"False SHOP_OUTLIER per product per 30 days: {per30}.",
        "",
        "## Band accuracy",
        "",
        f"MAPE of the band p50 against the fair area price, from day {ev.band_warmup_days} "
        f"(end-of-day snapshots): **{_pct(ev.band_mape, 2)}**; a band existed on "
        f"{_pct(ev.band_coverage, 0)} of product-days.",
        "",
        "| product | MAPE | median band mean age | median age of newest report |",
        "|---|---|---|---|",
    ]
    for product, mape in ev.band_mape_by_product.items():
        fresh = ev.freshness[product]
        lines.append(
            f"| {product} | {_pct(mape, 2)} | {_num(fresh['median_band_mean_age_h'])} h | "
            f"{_num(fresh['median_newest_report_age_h'])} h |"
        )
    lines += ["", "## Guardrail rejections", ""]
    if ev.rejections:
        lines += ["| code | count |", "|---|---|"]
        lines += [f"| {code} | {n} |" for code, n in ev.rejections.items()]
    else:
        lines.append("None.")
    kinds = ", ".join(f"{k} {n}" for k, n in sorted(ev.anomalies_by_kind.items())) or "none"
    lines += ["", f"Anomalies opened: {kinds}.", ""]
    return "\n".join(lines)


def write_report(ev: Evaluation, generated_at: datetime, out_dir: Path = OUT_DIR) -> Path:
    """Write ``<scenario>-<timestamp>.md`` and ``.json``; returns the Markdown path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = out_dir / f"{ev.scenario}-{generated_at:%Y%m%dT%H%M%S}"
    md = stem.with_suffix(".md")
    md.write_text(to_markdown(ev, generated_at), encoding="utf-8")
    stem.with_suffix(".json").write_text(
        json.dumps(to_json(ev), indent=2, default=str) + "\n", encoding="utf-8"
    )
    return md
