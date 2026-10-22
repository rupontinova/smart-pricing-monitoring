"""The ``spm`` command."""

from pathlib import Path

import typer
import uvicorn

import spm
from spm.config import get_settings
from spm.logging import configure_logging

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
def worker() -> None:
    """Run the event consumers."""
    _not_implemented("worker", "M13")


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
def seed() -> None:
    """Load catalog seeds."""
    _not_implemented("seed", "M04")
