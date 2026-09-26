"""Structured application configuration.

Two configuration surfaces are kept separate on purpose:

* Product settings (this module): ``APP_*`` environment variables, optionally read
  from a local ``.env`` file.
* Agno runtime settings: Agno's own ``AgnoAPISettings``, which reads Agno-defined
  variables such as ``OS_SECURITY_KEY``. See ``app.runtime.agentos``.

Model provider credentials are *not* product settings: they stay in the providers'
standard environment variables (``OPENAI_API_KEY``, ``ANTHROPIC_API_KEY``), which
Agno's model classes read themselves. No credentials have defaults.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, RedisDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["local", "test", "staging", "production"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
ModelProvider = Literal["disabled", "openai", "anthropic"]

# Environments meant for development and automated testing (not deployments).
DEVELOPMENT_ENVIRONMENTS: frozenset[str] = frozenset({"local", "test"})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="APP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    name: str = "commerce-ai-platform"
    environment: Environment = "local"
    debug: bool = False
    log_level: LogLevel = "INFO"

    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8000, ge=1, le=65535)

    # Required by the agent runtime; validated when the application is built.
    database_url: PostgresDsn | None = None
    redis_url: RedisDsn | None = None

    # PostgreSQL schema owned by Agno for its runtime tables.
    agno_db_schema: str = Field(default="agno_runtime", pattern=r"^[a-z_][a-z0-9_]*$")

    # Deployment-default model used by generic agents. Individual agents may use other
    # models later; this is a default, not a single-model architecture. There is no
    # default model ID on purpose: IDs change over time and must be chosen explicitly.
    default_model_provider: ModelProvider = "disabled"
    default_model_id: str | None = None

    @field_validator("default_model_id", mode="before")
    @classmethod
    def _blank_model_id_is_unset(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value.strip() if isinstance(value, str) else value

    @property
    def is_development(self) -> bool:
        return self.environment in DEVELOPMENT_ENVIRONMENTS


@lru_cache
def get_settings() -> Settings:
    return Settings()
