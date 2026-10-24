"""Scoring (MODULES.md M13): classify a reported price, resolve PENDING reports, settle
reputation, open anomalies and recompute the band, all in one transaction under a per
product × area advisory lock (so band updates never race).

``handle`` runs inside the consumer's unit of work and returns a ``ScoringOutcome``;
``after_commit`` publishes the outbox events and refreshes the hot cache once that commit
succeeded.
"""

import dataclasses
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

import structlog

from spm.constants import MARKET_SHIFT_WINDOW_H, SHOP_HISTORY_D
from spm.core.band import BAND_INCLUDED, SHOP_INCLUDED
from spm.core.classifier import (
    ClassificationContext,
    build_shift_evidence,
    classify,
    market_window_h,
)
from spm.core.enums import AnomalyKind, Classification, Direction, ScoringState
from spm.core.events import (
    AnomalyDetected,
    BandUpdated,
    Event,
    PriceReported,
    make_event,
)
from spm.core.models import Anomaly, Assessment, FairBand, Observation, Product, ShopPrice
from spm.core.rollup import compute_daily_stat, day_bounds, local_day
from spm.core.trust import (
    TrustDelta,
    corroboration_window_h,
    is_high_trust,
    resolve_pending,
    settle_normal,
)
from spm.ports import (
    AnomalyRepo,
    AssessmentRepo,
    BandRepo,
    Clock,
    IdGenerator,
    Metrics,
    OutboxRepo,
    ReporterRepo,
    StatsRepo,
)
from spm.services.band_service import BandService
from spm.services.catalog_cache import CatalogCache
from spm.services.outbox import OutboxPublisher

log = structlog.get_logger("spm.scoring")

SOURCE = "spm.worker.scoring"
BAD_UNIT_PENALTY_BETA = 0.5  # BAD_REPORT by relative bounds: may be an honest typo


class ScoringUnitOfWork(Protocol):
    @property
    def reporters(self) -> ReporterRepo: ...
    @property
    def assessments(self) -> AssessmentRepo: ...
    @property
    def anomalies(self) -> AnomalyRepo: ...
    @property
    def bands(self) -> BandRepo: ...
    @property
    def stats(self) -> StatsRepo: ...
    @property
    def outbox(self) -> OutboxRepo: ...
    async def advisory_lock(self, key: str) -> None: ...
    async def rollback(self) -> None: ...


@dataclass(slots=True)
class ScoringOutcome:
    report_id: UUID
    started: float  # perf_counter at the start of handle
    skipped: bool = False  # already scored (replay) or unknown report
    failed: bool = False  # bad data: assessment set to `failed`
    classification: Classification | None = None
    events: list[Event] = field(default_factory=list)
    band: FairBand | None = None
    prices: list[ShopPrice] = field(default_factory=list)


class ScoringService:
    def __init__(
        self,
        catalog: CatalogCache,
        bands: BandService,
        outbox_publisher: OutboxPublisher,
        clock: Clock,
        ids: IdGenerator,
        metrics: Metrics | None = None,
    ) -> None:
        self._catalog = catalog
        self._bands = bands
        self._outbox = outbox_publisher
        self._clock = clock
        self._ids = ids
        self._metrics = metrics

    # ------------------------------------------------------------------------ entry points
    async def handle(self, event: Event, uow: ScoringUnitOfWork) -> ScoringOutcome:
        started = time.perf_counter()
        payload = event.payload()
        if not isinstance(payload, PriceReported):
            raise ValueError(f"scoring got a {event.type} event")
        outcome = ScoringOutcome(payload.report_id, started)

        # Serialize scoring per product × area, then check the state under the lock: a replay
        # (or a concurrent duplicate) of an already scored report does nothing.
        await uow.advisory_lock(f"score:{payload.product_id}:{payload.area_id}")
        try:
            state, _ = await uow.assessments.get_state(payload.report_id)
        except LookupError:
            log.error("scoring_unknown_report", report_id=str(payload.report_id))
            outcome.skipped = True
            return outcome
        if state is not ScoringState.QUEUED:
            outcome.skipped = True
            return outcome

        try:
            await self._score(payload, event.correlation_id, uow, outcome)
        except ValueError as exc:
            # Bad data: record it and stop (raising would only make the bus retry forever).
            # Transient errors (DB/Redis) are not caught, so the bus retries them.
            await uow.rollback()
            await uow.assessments.mark_failed(payload.report_id, str(exc), self._clock.now())
            log.warning("scoring_failed", report_id=str(payload.report_id), reason=str(exc))
            outcome.failed, outcome.events, outcome.band = True, [], None
        return outcome

    async def after_commit(self, outcome: ScoringOutcome) -> None:
        """Publish outbox events and refresh the hot cache; metrics. Run after the commit."""
        if outcome.skipped:
            return
        await self._outbox.flush(outcome.events)
        if outcome.band is not None:
            await self._bands.publish_to_cache(outcome.band, outcome.prices)
        elapsed_ms = (time.perf_counter() - outcome.started) * 1000
        label = "failed" if outcome.failed else str(outcome.classification)
        if self._metrics is not None:
            self._metrics.count("reports_scored", 1, classification=label)
            self._metrics.timing("scoring_ms", elapsed_ms)
        log.info(
            "report_scored",
            report_id=str(outcome.report_id),
            classification=label,
            events=[e.type for e in outcome.events],
            scoring_ms=round(elapsed_ms, 1),
        )

    # ------------------------------------------------------------------------- the steps
    async def _score(
        self,
        payload: PriceReported,
        correlation_id: str | None,
        uow: ScoringUnitOfWork,
        outcome: ScoringOutcome,
    ) -> None:
        now = self._clock.now()
        catalog = await self._catalog.ensure_fresh()
        product = catalog.product(payload.product_id)
        area = catalog.area(payload.area_id)
        if product is None or area is None:
            raise ValueError(f"unknown product or area: {payload.product_id}/{payload.area_id}")
        reporter = await uow.reporters.get(payload.reporter_id)
        new = await uow.assessments.get_observation(payload.report_id)
        if reporter is None or new is None:
            raise ValueError("report or reporter missing")
        half_life = product.half_life_h
        window_h = corroboration_window_h(half_life)

        def event(type_: str, data: AnomalyDetected | BandUpdated) -> Event:
            return make_event(type_, data, SOURCE, correlation_id, self._clock, self._ids)

        # 3-4. Context: one area query covers shop history, market, shift evidence and
        #      corroboration; the 90-day median is a separate aggregate.
        lookback_h = max(
            SHOP_HISTORY_D * 24 + MARKET_SHIFT_WINDOW_H, market_window_h(half_life), window_h
        )
        area_obs = await uow.assessments.observations_for_area(
            product.id, area.id, now - timedelta(hours=lookback_h)
        )
        others = [o for o in area_obs if o.report_id != new.report_id]
        median_90d, n_90d = await uow.stats.area_median(
            product.id, area.id, now - timedelta(days=90)
        )
        ctx = self._context(
            now,
            product,
            new,
            is_high_trust(reporter.alpha, reporter.beta),
            median_90d,
            n_90d,
            others,
            [s.id for s in catalog.shops_in_area(area.id)],
        )

        # 5. Classify.
        assessment = classify(ctx)
        await uow.assessments.save(assessment, now)

        # 6. Pending resolution at this shop.
        scored = dataclasses.replace(new, classification=assessment.classification)
        pending = await uow.assessments.pending_for_shop(
            product.id, new.shop_id, new.observed_at - timedelta(hours=window_h)
        )
        resolutions = resolve_pending(
            [p for p in pending if p.report_id != new.report_id],
            scored,
            assessment.z_market,
            window_h,
        )
        deltas: list[TrustDelta] = []
        promoted: list[Observation] = []
        by_id = {p.report_id: p for p in pending}
        for res in resolutions:
            await uow.assessments.resolve(
                res.report_id, res.new_classification, res.resolved_by, now
            )
            deltas += res.deltas
            if res.new_classification is Classification.SHOP_OUTLIER:
                promoted.append(by_id[res.report_id])
        if promoted and assessment.classification is Classification.PENDING:
            # This report is outlying the same way as the outlier it just confirmed.
            assessment = dataclasses.replace(
                assessment,
                classification=Classification.SHOP_OUTLIER,
                reason=assessment.reason + "; corroborated by an earlier pending report",
            )
            await uow.assessments.save(assessment, now)
            scored = dataclasses.replace(scored, classification=assessment.classification)

        # 7. Reputation.
        label = assessment.classification
        if label in (Classification.NORMAL, Classification.MARKET_SHIFT):
            peers = await uow.assessments.unsettled_for_shop(
                product.id, new.shop_id, new.observed_at - timedelta(hours=window_h)
            )
            deltas += settle_normal(
                scored, [p for p in peers if p.report_id != new.report_id], window_h
            )
        elif label is Classification.BAD_REPORT and assessment.reason.startswith(
            "outside relative"
        ):
            deltas.append(
                TrustDelta(new.reporter_id, 0.0, BAD_UNIT_PENALTY_BETA, new.report_id, "bad_unit")
            )
        for d in deltas:
            await uow.reporters.add_evidence(d.reporter_id, d.d_alpha, d.d_beta)
        await uow.assessments.mark_trust_settled(d.report_id for d in deltas)

        # 8. Anomalies (deduplicated while open).
        events: list[Event] = []
        anomaly = self._anomaly(assessment, new, promoted, product.id, area.id, now)
        if anomaly is not None:
            dedup_key = (
                f"{anomaly.kind.value}:{area.id}:{product.id}:"
                f"{anomaly.shop_id or '*'}:{anomaly.direction.value}"
            )
            if await uow.anomalies.open_if_absent(anomaly, dedup_key):
                events.append(
                    event(
                        "anomaly.detected",
                        AnomalyDetected(
                            anomaly_id=anomaly.id,
                            kind=anomaly.kind,
                            direction=anomaly.direction,
                            product_id=anomaly.product_id,
                            area_id=anomaly.area_id,
                            shop_id=anomaly.shop_id,
                            observed_price=anomaly.observed_price,
                            reference_price=anomaly.reference_price,
                            magnitude_pct=anomaly.magnitude_pct,
                            report_ids=list(anomaly.report_ids),
                            detected_at=anomaly.detected_at,
                        ),
                    )
                )
                if anomaly.kind is AnomalyKind.MARKET_SHIFT:
                    # The band restarts from where the shift began (once per shift).
                    await uow.bands.set_rebase(
                        product.id, area.id, now - timedelta(hours=MARKET_SHIFT_WINDOW_H)
                    )

        # 9. Band and shop prices.
        band, prices = await self._bands.recompute(uow, product, area.id, now)
        if band.p25 is not None and band.p50 is not None and band.p75 is not None:
            events.append(
                event(
                    "band.updated",
                    BandUpdated(
                        product_id=product.id,
                        area_id=area.id,
                        p25=band.p25,
                        p50=band.p50,
                        p75=band.p75,
                        confidence=band.confidence,
                        computed_at=band.computed_at,
                    ),
                )
            )

        # 10. Daily rollups for the report's (area-local) day: area-wide and this shop.
        day = local_day(new.observed_at, area.timezone)
        day_start, _ = day_bounds(day, area.timezone)
        day_obs = await uow.assessments.observations_for_area(product.id, area.id, day_start)
        rows = [
            compute_daily_stat(
                product.id,
                area.id,
                None,
                day_obs,
                day,
                area.timezone,
                now,
                half_life,
                BAND_INCLUDED,
            ),
            compute_daily_stat(
                product.id,
                area.id,
                new.shop_id,
                day_obs,
                day,
                area.timezone,
                now,
                half_life,
                SHOP_INCLUDED,
            ),
        ]
        await uow.stats.upsert_daily([r for r in rows if r is not None])

        # 11. Outbox (published by after_commit).
        await uow.outbox.add(events)
        outcome.classification = assessment.classification
        outcome.events, outcome.band, outcome.prices = events, band, prices

    @staticmethod
    def _context(
        now: datetime,
        product: Product,
        new: Observation,
        high_trust: bool,
        median_90d: float | None,
        n_90d: int,
        others: list[Observation],
        active_shop_ids: list[str],
    ) -> ClassificationContext:
        half_life = product.half_life_h

        def age_h(o: Observation) -> float:
            return (now - o.observed_at).total_seconds() / 3600

        by_shop: dict[str, list[Observation]] = defaultdict(list)
        for o in others:
            by_shop[o.shop_id].append(o)
        for shop_id in active_shop_ids:
            by_shop.setdefault(shop_id, [])
        same_shop = by_shop.get(new.shop_id, [])
        window_h = corroboration_window_h(half_life)
        return ClassificationContext(
            now=now,
            product=product,
            report=new,
            reporter_high_trust=high_trust,
            area_median_90d=median_90d,
            area_n_90d=n_90d,
            shop_history=[o for o in same_shop if age_h(o) <= SHOP_HISTORY_D * 24],
            market=[
                o
                for o in others
                if o.shop_id != new.shop_id and age_h(o) <= market_window_h(half_life)
            ],
            same_shop_recent=[
                o
                for o in same_shop
                if abs((o.observed_at - new.observed_at).total_seconds()) <= window_h * 3600
            ],
            shift_candidates=build_shift_evidence(by_shop, now),
            active_shops_in_area=len(active_shop_ids),
        )

    def _anomaly(
        self,
        assessment: Assessment,
        new: Observation,
        promoted: list[Observation],
        product_id: str,
        area_id: str,
        now: datetime,
    ) -> Anomaly | None:
        label = assessment.classification
        report_ids: tuple[UUID, ...]
        if label is Classification.MARKET_SHIFT:
            kind, shop_id = AnomalyKind.MARKET_SHIFT, None
            observed, report_ids = new.norm_price, (new.report_id,)
        elif label is Classification.SHOP_OUTLIER:
            kind, shop_id = AnomalyKind.SHOP_OUTLIER, new.shop_id
            observed = new.norm_price
            report_ids = (new.report_id, *(p.report_id for p in promoted))
        elif promoted:  # a pending report confirmed by this one, which itself isn't outlying
            kind, shop_id = AnomalyKind.SHOP_OUTLIER, promoted[0].shop_id
            observed = promoted[0].norm_price
            report_ids = tuple(p.report_id for p in promoted)
        else:
            return None
        reference = assessment.reference_price
        if reference is None or reference <= 0:
            return None  # can't size it without a reference (no market view)
        direction = assessment.direction or (
            Direction.UP if observed > reference else Direction.DOWN
        )
        return Anomaly(
            id=self._ids.new(),
            kind=kind,
            direction=direction,
            product_id=product_id,
            area_id=area_id,
            shop_id=shop_id,
            observed_price=observed,
            reference_price=reference,
            magnitude_pct=(observed / reference - 1) * 100,
            report_ids=report_ids,
            detected_at=now,
        )
