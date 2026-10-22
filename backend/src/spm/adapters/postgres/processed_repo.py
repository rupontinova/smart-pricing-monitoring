"""``PgProcessedEventRepo``: which consumer already handled which event (idempotency)."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import exists, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from spm.adapters.postgres.tables import processed_events


class PgProcessedEventRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def seen(self, consumer: str, event_id: UUID) -> bool:
        query = select(
            exists().where(
                processed_events.c.consumer == consumer, processed_events.c.event_id == event_id
            )
        )
        return bool((await self._session.execute(query)).scalar())

    async def mark(self, consumer: str, event_id: UUID, at: datetime) -> bool:
        """INSERT … ON CONFLICT DO NOTHING RETURNING: True only for the first delivery.

        A concurrent transaction marking the same event blocks on the row until this one ends,
        then sees the conflict.
        """
        stmt = (
            insert(processed_events)
            .values(consumer=consumer, event_id=event_id, processed_at=at)
            .on_conflict_do_nothing()
            .returning(processed_events.c.event_id)
        )
        return (await self._session.execute(stmt)).first() is not None
