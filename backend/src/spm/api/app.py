"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from spm import __version__
from spm.api.errors import install_exception_handlers
from spm.api.middleware import (
    REQUEST_ID_HEADER,
    BodySizeLimitMiddleware,
    RequestIdMiddleware,
    ServerTimingMiddleware,
    UnhandledErrorMiddleware,
)
from spm.api.routers import catalog, health
from spm.config import Settings, get_settings
from spm.container import Container, build_container
from spm.logging import configure_logging

log = structlog.get_logger("spm.api")


async def warm_up(container: Container) -> None:
    """Best-effort startup work: load the catalog and rebuild ``spm:geo:shops`` (§3.2).

    Failures are logged, not raised, so the API still starts (``/readyz`` reports the outage).
    """
    if container.catalog is None or container.shop_geo is None:
        return
    try:
        await container.catalog.ensure_fresh()
        await container.shop_geo.load_shop_geo(container.catalog.shops())
    except Exception:
        log.warning("startup_warmup_failed", exc_info=True)


def create_app(settings: Settings | None = None, *, container: Container | None = None) -> FastAPI:
    """Build the app. A pre-built ``container`` (tests) is used as-is and not closed by the app."""
    settings = settings or (container.settings if container else get_settings())
    configure_logging(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owned = container is None
        app.state.container = await build_container(settings) if owned else container
        await warm_up(app.state.container)
        try:
            yield
        finally:
            if owned:
                await app.state.container.close()

    app = FastAPI(title="Smart Pricing Monitor", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    install_exception_handlers(app)

    # Added innermost first. Request order: RequestId → ServerTiming → CORS → BodySizeLimit
    # → UnhandledError → (FastAPI exception handlers) → routers.
    app.add_middleware(UnhandledErrorMiddleware)
    app.add_middleware(BodySizeLimitMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[REQUEST_ID_HEADER, "Server-Timing", "Retry-After"],
    )
    app.add_middleware(ServerTimingMiddleware)
    app.add_middleware(RequestIdMiddleware)

    app.include_router(health.router)
    app.include_router(catalog.router)
    return app
