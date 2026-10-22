"""``PgOutboxRepo``: events written in the same transaction as the business rows."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from spm.adapters.postgres.tables import outbox
from spm.core.events import Event


class PgOutboxRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, events: list[Event]) -> None:
        if not events:
            return
        rows = [
            {
                "event_id": e.id,
                "event_type": e.type,
                # JSONB: the JSON-mode envelope (datetimes as ISO strings).
                "payload": {**e.to_dict(), "occurred_at": e.occurred_at.isoformat()},
                "created_at": e.occurred_at,
            }
            for e in events
        ]
        await self._session.execute(insert(outbox).on_conflict_do_nothing(), rows)

    async def mark_published(self, event_ids: list[UUID], at: datetime) -> None:
        if event_ids:
            await self._session.execute(
                update(outbox).where(outbox.c.event_id.in_(event_ids)).values(published_at=at)
            )

    async def unpublished(self, older_than: datetime, limit: int) -> list[Event]:
        """Oldest first. Rows stay locked (SKIP LOCKED) until the transaction ends, so concurrent
        sweepers never pick the same rows."""
        query = (
            select(outbox.c.payload)
            .where(outbox.c.published_at.is_(None), outbox.c.created_at < older_than)
            .order_by(outbox.c.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        rows = await self._session.execute(query)
        return [Event.from_dict(payload) for (payload,) in rows.all()]

    async def bump_attempts(self, event_ids: list[UUID]) -> None:
        if event_ids:
            await self._session.execute(
                update(outbox)
                .where(outbox.c.event_id.in_(event_ids))
                .values(attempts=outbox.c.attempts + 1)
            )
