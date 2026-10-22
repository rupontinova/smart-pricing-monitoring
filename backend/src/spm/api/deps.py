"""FastAPI dependencies shared by routers."""

from typing import Annotated, cast

from fastapi import Depends, Request

from spm.config import Settings
from spm.container import Container
from spm.core.errors import ServiceUnavailable
from spm.services.catalog import CatalogService
from spm.services.catalog_cache import CatalogCache


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
