"""FastAPI dependencies shared by routers."""

import hmac
from typing import Annotated, Any, cast

from fastapi import Depends, Request

from spm.config import Settings
from spm.container import Container
from spm.core.errors import Forbidden, ServiceUnavailable, Unauthorized
from spm.core.models import Reporter
from spm.core.privacy import hash_ip
from spm.services.auth_service import AuthService
from spm.services.catalog import CatalogService
from spm.services.catalog_cache import CatalogCache
from spm.services.guardrail_service import GuardrailService
from spm.services.outbox import OutboxPublisher
from spm.services.report_service import ReportService


def get_container(request: Request) -> Container:
    return cast(Container, request.app.state.container)


def get_settings_dep(container: Annotated[Container, Depends(get_container)]) -> Settings:
    return container.settings


ContainerDep = Annotated[Container, Depends(get_container)]
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]


async def get_catalog(container: ContainerDep) -> CatalogCache:
    """The catalog cache, refreshed if stale (so routers can use its sync getters)."""
    if container.catalog is None:
        raise ServiceUnavailable("The catalog is not configured (no database).")
    return await container.catalog.ensure_fresh()


CatalogDep = Annotated[CatalogCache, Depends(get_catalog)]


def get_catalog_service(container: ContainerDep, catalog: CatalogDep) -> CatalogService:
    return CatalogService(catalog, container.shop_geo)


CatalogServiceDep = Annotated[CatalogService, Depends(get_catalog_service)]


# ------------------------------------------------------------------------------ M08: auth & reports
def client_ip(request: Request) -> str:
    """First hop of ``X-Forwarded-For`` (set by CloudFront/API Gateway), else the TCP peer."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else "unknown"


def get_ip_hash(request: Request, settings: SettingsDep) -> str:
    return hash_ip(settings.ip_hash_salt.get_secret_value(), client_ip(request))


IpHashDep = Annotated[str, Depends(get_ip_hash)]


def _uow_factory(container: Container) -> Any:
    if container.uow_factory is None:
        raise ServiceUnavailable("Reports are not available (no database).")
    return container.uow_factory


def get_auth_service(container: ContainerDep) -> AuthService:
    if container.tokens is None:
        raise ServiceUnavailable("Authentication is not configured.")
    return AuthService(
        _uow_factory(container), container.tokens, container.ids, container.clock,
        container.rate_limiter,
    )  # fmt: skip


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]


async def current_reporter(request: Request, auth: AuthServiceDep) -> Reporter:
    """``Authorization: Bearer <device JWT>`` -> reporter (401 invalid, 403 banned)."""
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise Unauthorized("Missing bearer token.")
    return await auth.authenticate(token.strip())


ReporterDep = Annotated[Reporter, Depends(current_reporter)]


def get_report_service(container: ContainerDep, catalog: CatalogDep) -> ReportService:
    uow_factory = _uow_factory(container)
    if container.rate_limiter is None or container.bus is None:
        raise ServiceUnavailable("Reports are not available (no Redis / event bus).")
    guardrails = GuardrailService(catalog, container.rate_limiter, uow_factory, container.clock)
    publisher = OutboxPublisher(container.bus, uow_factory, container.clock)
    return ReportService(
        guardrails, uow_factory, publisher, container.ids, container.clock, container.metrics
    )


ReportServiceDep = Annotated[ReportService, Depends(get_report_service)]


def require_admin(request: Request, settings: SettingsDep) -> None:
    """``X-Admin-Key`` must match ``SPM_ADMIN_API_KEY`` (403 otherwise)."""
    given = request.headers.get("x-admin-key", "")
    expected = settings.admin_api_key.get_secret_value()
    if not given or not hmac.compare_digest(given.encode(), expected.encode()):
        raise Forbidden("Invalid admin key.")


AdminDep = Annotated[None, Depends(require_admin)]
