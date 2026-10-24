from pathlib import Path
from typing import Any

import pytest
from mangum import Mangum
from typer.testing import CliRunner

from spm.adapters.ids import UuidV7Generator
from spm.config import get_settings
from spm.entrypoints import cli

runner = CliRunner()


def test_cli_api_runs_uvicorn_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    monkeypatch.setattr("uvicorn.run", lambda *a, **kw: calls.append((a, kw)))
    result = runner.invoke(cli.app, ["api", "--port", "8123", "--no-reload"])
    assert result.exit_code == 0, result.output
    ((args, kwargs),) = calls
    assert args == ("spm.api.app:create_app",)
    assert kwargs["factory"] is True
    assert kwargs["port"] == 8123
    assert kwargs["reload"] is False


def test_cli_migrate_upgrades_to_head(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        "spm.adapters.postgres.migrate.upgrade", lambda rev, url=None: calls.append((rev, url))
    )
    result = runner.invoke(cli.app, ["migrate"])
    assert result.exit_code == 0, result.output
    assert calls == [("head", get_settings().database_url)]


def test_cli_seed_rejects_invalid_seed_dir(tmp_path: Path) -> None:
    result = runner.invoke(cli.app, ["seed", "--dir", str(tmp_path)])
    assert result.exit_code == 1
    assert "Invalid seed files" in result.output


def test_cli_worker_without_database_exits_with_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPM_DATABASE_URL", "")
    monkeypatch.setenv("SPM_REDIS_URL", "")
    get_settings.cache_clear()
    try:
        result = runner.invoke(cli.app, ["worker", "--no-debug-log-events"])
    finally:
        get_settings.cache_clear()
    assert result.exit_code == 1
    assert "Worker can't start" in result.output


def test_lambda_handler_is_mangum() -> None:
    from spm.entrypoints.lambda_api import handler

    assert isinstance(handler, Mangum)


def test_uuid7_generator_is_time_ordered() -> None:
    ids = UuidV7Generator()
    first, second = ids.new(), ids.new()
    assert first.version == 7
    assert second > first
