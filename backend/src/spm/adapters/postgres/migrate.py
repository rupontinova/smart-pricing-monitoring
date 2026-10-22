"""Programmatic Alembic: used by ``spm migrate`` and the integration test fixtures."""

from pathlib import Path

from alembic import command
from alembic.config import Config

# src/spm/adapters/postgres/migrate.py -> backend/alembic.ini
ALEMBIC_INI = Path(__file__).resolve().parents[4] / "alembic.ini"


def alembic_config(database_url: str | None = None) -> Config:
    """Alembic config; ``database_url`` overrides ``SPM_DATABASE_URL``."""
    config = Config(str(ALEMBIC_INI))
    if database_url:
        config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def upgrade(revision: str = "head", database_url: str | None = None) -> None:
    command.upgrade(alembic_config(database_url), revision)


def downgrade(revision: str, database_url: str | None = None) -> None:
    command.downgrade(alembic_config(database_url), revision)
