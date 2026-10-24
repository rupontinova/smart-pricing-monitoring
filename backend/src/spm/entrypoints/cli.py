"""The ``spm`` command."""

from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer
import uvicorn

import spm
from spm.config import Settings, get_settings
from spm.logging import configure_logging

if TYPE_CHECKING:
    from spm.adapters.catalog_yaml import SeedCatalog
    from spm.adapters.postgres.catalog_seed import SeedReport

app = typer.Typer(name="spm", help="Smart Pricing Monitor", no_args_is_help=True)


@app.command()
def api(
    host: str = typer.Option("127.0.0.1", help="Bind address."),
    port: int = typer.Option(8000, help="Bind port."),
    reload: bool | None = typer.Option(
        None,
        "--reload/--no-reload",
        help="Auto-reload on code changes (default: on when SPM_ENV=local).",
    ),
) -> None:
    """Run the HTTP API with uvicorn."""
    settings = get_settings()
    configure_logging(settings)
    uvicorn.run(
        "spm.api.app:create_app",
        factory=True,
        host=host,
        port=port,
        reload=settings.env == "local" if reload is None else reload,
        reload_dirs=[str(Path(spm.__file__).parent)],
        log_config=None,  # keep our structlog configuration
    )


def _not_implemented(command: str, module: str) -> None:
    typer.echo(f"spm {command}: not implemented yet (arrives in {module})")


@app.command()
def worker(
    debug_log_events: Annotated[
        bool | None,
        typer.Option(
            "--debug-log-events/--no-debug-log-events",
            help="Log every event (default: on when SPM_ENV=local).",
        ),
    ] = None,
) -> None:
    """Run the event consumers until SIGINT/SIGTERM."""
    import asyncio

    from spm.container import build_container
    from spm.workers.runtime import ConsumerSpec, log_event, run_consumers
    from spm.workers.scoring import consumer_specs

    settings = get_settings()
    configure_logging(settings)
    log_events = debug_log_events if debug_log_events is not None else settings.env == "local"

    async def main() -> None:
        container = await build_container(settings)
        try:
            specs = consumer_specs(container)
            if log_events:
                specs.append(ConsumerSpec("debug-log", log_event))
            await run_consumers(container, specs)
        finally:
            await container.close()

    try:
        asyncio.run(main())
    except RuntimeError as exc:  # e.g. no database / Redis configured for scoring
        typer.echo(f"Worker can't start: {exc}", err=True)
        raise typer.Exit(1) from exc


@app.command()
def scheduler() -> None:
    """Run the scheduler."""
    _not_implemented("scheduler", "M17")


@app.command()
def migrate(
    revision: str = typer.Option("head", help="Target revision (upgrade)."),
) -> None:
    """Apply database migrations (alembic upgrade)."""
    from spm.adapters.postgres.migrate import upgrade

    settings = get_settings()
    configure_logging(settings)
    upgrade(revision, settings.database_url)
    typer.echo(f"Database migrated to {revision}.")


@app.command()
def seed(
    seeds_dir: Annotated[
        Path | None,
        typer.Option("--dir", help="Directory with areas.yaml, products.yaml, shops.yaml."),
    ] = None,
) -> None:
    """Upsert the catalog seeds into Postgres and rebuild the Redis shop geo index."""
    import asyncio

    from spm.adapters.catalog_yaml import SEEDS_DIR, SeedError, load_catalog

    settings = get_settings()
    configure_logging(settings)
    try:
        catalog = load_catalog(seeds_dir or SEEDS_DIR)
    except SeedError as exc:
        typer.echo(f"Invalid seed files: {exc}", err=True)
        raise typer.Exit(1) from exc
    report = asyncio.run(_seed(settings, catalog))
    typer.echo(
        f"Seeded {report.areas} areas, {report.products} products, {report.aliases} aliases, "
        f"{report.shops} shops; {report.geo_shops} shops in the geo index."
    )


async def _seed(settings: Settings, catalog: "SeedCatalog") -> "SeedReport":
    from spm.adapters.postgres.catalog_seed import seed_catalog
    from spm.adapters.postgres.db import make_engine, make_session_factory
    from spm.adapters.redis.hot_cache import RedisHotCache
    from spm.container import make_redis

    engine = make_engine(settings)
    redis = make_redis(settings.redis_url)
    try:
        return await seed_catalog(catalog, make_session_factory(engine), RedisHotCache(redis))
    finally:
        await redis.aclose()
        await engine.dispose()
