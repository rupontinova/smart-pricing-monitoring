"""``PgReporterRepo``: anonymous device reporters and their Beta(α, β) reputation."""

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import ColumnElement, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from spm.adapters.postgres.tables import reporters
from spm.constants import LAST_SEEN_UPDATE_INTERVAL_H
from spm.core.models import Reporter


def _reporter(row: Mapping[str, Any]) -> Reporter:
    return Reporter(
        id=row["id"],
        device_id=row["device_id"],
        alpha=float(row["alpha"]),
        beta=float(row["beta"]),
        reports_total=row["reports_total"],
        banned=row["banned"],
        created_at=row["created_at"],
        last_seen_at=row["last_seen_at"],
    )


class PgReporterRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _one(self, where: ColumnElement[bool]) -> Reporter | None:
        row = (await self._session.execute(select(reporters).where(where))).mappings().first()
        return _reporter(dict(row)) if row else None

    async def get(self, reporter_id: UUID) -> Reporter | None:
        return await self._one(reporters.c.id == reporter_id)

    async def get_by_device(self, device_id: str) -> Reporter | None:
        return await self._one(reporters.c.device_id == device_id)

    async def create(self, reporter: Reporter) -> None:
        await self._session.execute(
            insert(reporters)
            .values(
                id=reporter.id,
                device_id=reporter.device_id,
                alpha=reporter.alpha,
                beta=reporter.beta,
                reports_total=reporter.reports_total,
                banned=reporter.banned,
                created_at=reporter.created_at,
                last_seen_at=reporter.last_seen_at,
            )
            .on_conflict_do_nothing(index_elements=[reporters.c.device_id])
        )

    async def touch(self, reporter_id: UUID, at: datetime) -> None:
        cutoff = at - timedelta(hours=LAST_SEEN_UPDATE_INTERVAL_H)
        await self._session.execute(
            update(reporters)
            .where(
                reporters.c.id == reporter_id,
                or_(reporters.c.last_seen_at.is_(None), reporters.c.last_seen_at < cutoff),
            )
            .values(last_seen_at=at)
        )

    async def add_evidence(self, reporter_id: UUID, d_alpha: float, d_beta: float) -> None:
        await self._session.execute(
            update(reporters)
            .where(reporters.c.id == reporter_id)
            .values(alpha=reporters.c.alpha + d_alpha, beta=reporters.c.beta + d_beta)
        )

    async def trust_many(self, ids: Iterable[UUID]) -> dict[UUID, float]:
        wanted = list(set(ids))
        if not wanted:
            return {}
        rows = await self._session.execute(
            select(reporters.c.id, reporters.c.alpha, reporters.c.beta).where(
                reporters.c.id.in_(wanted)
            )
        )
        return {rid: float(a) / (float(a) + float(b)) for rid, a, b in rows.all()}
