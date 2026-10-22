"""Runtime configuration, read from ``SPM_*`` environment variables (MODULES.md §2.6)."""

from functools import lru_cache
from typing import Annotated, Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Placeholder secret value shipped in `.env.example`; refused outside local/test.
INSECURE_DEFAULT_SECRET = "change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SPM_", env_file=".env", extra="ignore")

    env: Literal["local", "test", "aws"] = "local"

    # An empty URL means "not configured": the dependency is skipped (e.g. by /readyz).
    database_url: str = "postgresql+asyncpg://spm:spm@localhost:5433/spm"
    db_pool_size: int = Field(default=5, ge=1)
    redis_url: str = "redis://localhost:6380/0"

    event_bus: Literal["memory", "redis", "eventbridge"] = "redis"
    notifier: Literal["console", "smtp", "sns"] = "console"
    metrics: Literal["log", "emf"] = "log"

    jwt_secret: SecretStr = SecretStr(INSECURE_DEFAULT_SECRET)
    jwt_ttl_d: int = Field(default=90, ge=1)
    admin_api_key: SecretStr = SecretStr(INSECURE_DEFAULT_SECRET)
    ip_hash_salt: SecretStr = SecretStr(INSECURE_DEFAULT_SECRET)

    currency: str = "BDT"
    default_area: str = "demo-central"
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173"]

    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_from: str = "alerts@spm.local"

    vapid_public_key: str = ""
    vapid_private_key: SecretStr = SecretStr("")
    vapid_subject: str = ""

    aws_event_bus_name: str = "spm-bus"
    sns_topic_arn: str = ""

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @model_validator(mode="after")
    def _secrets_must_be_real_in_aws(self) -> Self:
        if self.env != "aws":
            return self
        weak = [
            name
            for name in ("jwt_secret", "admin_api_key", "ip_hash_salt")
            if getattr(self, name).get_secret_value() in ("", INSECURE_DEFAULT_SECRET)
        ]
        if weak:
            raise ValueError(f"SPM_ENV=aws requires non-default secrets: {', '.join(weak)}")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
