"""Device auth, report submission and the reporter's own data."""

from uuid import UUID

from fastapi import APIRouter, Response, status

from spm.api.deps import (
    AdminDep,
    AuthServiceDep,
    IpHashDep,
    ReporterDep,
    ReportServiceDep,
    SettingsDep,
)
from spm.api.errors import current_request_id
from spm.api.schemas.reports import (
    BackfillReportIn,
    DeviceIn,
    DeviceTokenOut,
    MeOut,
    ReportIn,
    ReportOut,
    ReportStatusOut,
)
from spm.core.errors import Forbidden

router = APIRouter(prefix="/v1", tags=["reports"])


@router.post("/auth/device")
async def register_device(
    body: DeviceIn, auth: AuthServiceDep, ip_hash: IpHashDep
) -> DeviceTokenOut:
    """Anonymous sign-in: the same device id always maps to the same reporter."""
    return DeviceTokenOut.of(await auth.register_device(str(body.device_id), ip_hash))


@router.post(
    "/reports",
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        200: {"description": "Replay or duplicate of an existing report", "model": ReportOut}
    },
)
async def submit_report(
    body: ReportIn,
    response: Response,
    reporter: ReporterDep,
    service: ReportServiceDep,
    ip_hash: IpHashDep,
) -> ReportOut:
    result = await service.submit(
        reporter, body.to_command(ip_hash), request_id=current_request_id()
    )
    if result.duplicate:
        response.status_code = status.HTTP_200_OK
    return ReportOut.of(result)


@router.get("/reports/{report_id}")
async def report_status(
    report_id: UUID, reporter: ReporterDep, service: ReportServiceDep
) -> ReportStatusOut:
    return ReportStatusOut.of(await service.status(reporter, report_id))


@router.get("/me")
async def me(reporter: ReporterDep) -> MeOut:
    return MeOut.of(reporter)


@router.post(
    "/admin/reports/backfill",
    status_code=status.HTTP_202_ACCEPTED,
    tags=["admin"],
    responses={
        200: {"description": "Replay or duplicate of an existing report", "model": ReportOut}
    },
)
async def backfill_report(
    body: BackfillReportIn,
    response: Response,
    _admin: AdminDep,
    settings: SettingsDep,
    auth: AuthServiceDep,
    service: ReportServiceDep,
) -> ReportOut:
    """Simulator path: any reporter (by device id), past observations, no rate limits."""
    if settings.env == "aws" and not settings.allow_backfill:
        raise Forbidden("Backfill is disabled (set SPM_ALLOW_BACKFILL=true).")
    reporter = await auth.reporter_for_device(body.reporter_device_id)
    result = await service.submit(
        reporter, body.to_command(None), request_id=current_request_id(), allow_backfill=True
    )
    if result.duplicate:
        response.status_code = status.HTTP_200_OK
    return ReportOut.of(result)
