"""``PgUnitOfWork``: one ``AsyncSession`` (one transaction) shared by every repository.

Repository attributes are wired here as their repositories are built: ``reporters`` and
``reports`` (M08), ``outbox`` and ``processed`` (M07), and the read parts of ``assessments`` and
``bands`` that M08 needs (the rest arrives with scoring, M10 to M13).
"""

from types import TracebackType
from typing import Self

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from spm.adapters.postgres.assessment_repo import PgAssessmentRepo
from spm.adapters.postgres.band_repo import PgBandRepo
from spm.adapters.postgres.outbox_repo import PgOutboxRepo
from spm.adapters.postgres.processed_repo import PgProcessedEventRepo
from spm.adapters.postgres.report_repo import PgReportRepo
from spm.adapters.postgres.reporter_repo import PgReporterRepo


class PgUnitOfWork:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._session: AsyncSession | None = None

    @property
    def session(self) -> AsyncSession:
        if self._session is None:
            raise RuntimeError("PgUnitOfWork used outside 'async with'")
        return self._session

    async def __aenter__(self) -> Self:
        self._session = self._session_factory()
        self.reporters = PgReporterRepo(self._session)
        self.reports = PgReportRepo(self._session)
        self.assessments = PgAssessmentRepo(self._session)
        self.bands = PgBandRepo(self._session)
        self.outbox = PgOutboxRepo(self._session)
        self.processed = PgProcessedEventRepo(self._session)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        # Anything not explicitly committed is rolled back.
        try:
            await self.session.rollback()
        finally:
            await self.session.close()
            self._session = None

    async def commit(self) -> None:
        await self.session.commit()

    async def rollback(self) -> None:
        await self.session.rollback()

    async def advisory_lock(self, key: str) -> None:
        """Transaction-scoped lock; released on commit or rollback."""
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key}
        )
