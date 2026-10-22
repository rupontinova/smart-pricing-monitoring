"""``PgAssessmentRepo``: the mutable scoring view.

M08 implements ``get_state``; scoring (M10 to M13) adds the rest of ``AssessmentRepo``.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from spm.adapters.postgres.tables import report_assessments
from spm.core.enums import Classification, ScoringState


class PgAssessmentRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_state(self, report_id: UUID) -> tuple[ScoringState, Classification | None]:
        row = (
            await self._session.execute(
                select(
                    report_assessments.c.scoring_state, report_assessments.c.classification
                ).where(report_assessments.c.report_id == report_id)
            )
        ).first()
        if row is None:
            raise LookupError(f"no assessment for report {report_id}")
        return ScoringState(row[0]), Classification(row[1]) if row[1] is not None else None
