import pytest
from pydantic import ValidationError

from spm.config import Settings, get_settings
from tests.conftest import make_settings


def test_settings_defaults_match_plan() -> None:
    s = Settings(_env_file=None)
    assert s.env == "local"
    assert s.database_url == "postgresql+asyncpg://spm:spm@localhost:5433/spm"
    assert s.redis_url == "redis://localhost:6380/0"
    assert s.currency == "BDT"
    assert s.cors_origins == ["http://localhost:5173"]
    assert s.jwt_ttl_d == 90


def test_settings_read_from_env_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPM_CURRENCY", "EUR")
    monkeypatch.setenv("SPM_CORS_ORIGINS", "http://a.test, http://b.test")
    s = Settings(_env_file=None)
    assert s.currency == "EUR"
    assert s.cors_origins == ["http://a.test", "http://b.test"]


def test_settings_refuse_default_secrets_when_aws() -> None:
    with pytest.raises(ValidationError, match="jwt_secret, admin_api_key, ip_hash_salt"):
        make_settings(env="aws")


def test_settings_refuse_empty_secret_when_aws() -> None:
    with pytest.raises(ValidationError, match="ip_hash_salt"):
        make_settings(env="aws", jwt_secret="a" * 32, admin_api_key="b" * 32, ip_hash_salt="")


def test_settings_accept_real_secrets_when_aws() -> None:
    s = make_settings(env="aws", jwt_secret="a" * 32, admin_api_key="b" * 32, ip_hash_salt="c" * 32)
    assert s.jwt_secret.get_secret_value() == "a" * 32
    assert "a" * 32 not in repr(s)


def test_get_settings_is_cached() -> None:
    assert get_settings() is get_settings()


def test_logging_silences_duplicate_access_log() -> None:
    import logging

    from spm.logging import configure_logging

    configure_logging(make_settings())
    access = logging.getLogger("uvicorn.access")
    assert access.propagate is False
    assert not access.handlers
