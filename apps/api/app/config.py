"""Structured application configuration.

Two configuration surfaces are kept separate on purpose:

* Product settings (this module): ``APP_*`` environment variables, optionally read
  from a local ``.env`` file.
* Agno runtime settings: Agno's own ``AgnoAPISettings``, which reads Agno-defined
  variables such as ``OS_SECURITY_KEY``. See ``app.runtime.agentos``.

No credentials have defaults.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, RedisDsn
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["local", "test", "staging", "production"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


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


@lru_cache
def get_settings() -> Settings:
    return Settings()
