"""Liveness (``/healthz``) and readiness (``/readyz``) probes."""

import asyncio
from collections.abc import Awaitable, Callable

import structlog
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from spm import __version__
from spm.adapters.probes import ping_engine, ping_redis
from spm.api.deps import ContainerDep
from spm.api.errors import problem
from spm.api.schemas.health import CheckStatus, HealthOut, ReadyOut

router = APIRouter(tags=["health"])
log = structlog.get_logger("spm.api.health")


@router.get("/healthz")
async def healthz() -> HealthOut:
    return HealthOut(version=__version__)


@router.get(
    "/readyz",
    response_model=ReadyOut,
    responses={503: {"description": "A configured dependency is down"}},
)
async def readyz(container: ContainerDep) -> ReadyOut | JSONResponse:
    settings = container.settings
    engine = container.db_engine
    probes: dict[str, Callable[[], Awaitable[None]] | None] = {
        "db": (lambda: ping_engine(engine)) if engine is not None else None,
        # Redis has no shared client yet (M06/M07); probe the URL with a one-shot connection.
        "redis": (lambda: ping_redis(settings.redis_url)) if settings.redis_url else None,
    }
    configured = {name: probe for name, probe in probes.items() if probe is not None}
    results = await asyncio.gather(
        *(probe() for probe in configured.values()), return_exceptions=True
    )

    checks: dict[str, CheckStatus] = {name: "skipped" for name in probes}
    for name, result in zip(configured, results, strict=True):
        if isinstance(result, BaseException):
            log.warning("readiness_check_failed", check=name, error=repr(result))
            checks[name] = "error"
        else:
            checks[name] = "ok"

    if "error" in checks.values():
        failed = ", ".join(name for name, status in checks.items() if status == "error")
        return problem(
            503,
            "SERVICE_UNAVAILABLE",
            "Service unavailable",
            f"Unreachable: {failed}",
            extra={"checks": checks},
        )
    return ReadyOut(checks=checks)
