"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

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
from spm.api.routers import health
from spm.config import Settings, get_settings
from spm.container import build_container
from spm.logging import configure_logging


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        container = await build_container(settings)
        app.state.container = container
        try:
            yield
        finally:
            await container.close()

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
    return app
