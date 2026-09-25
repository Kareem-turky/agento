"""Structured application configuration.

Settings are loaded from environment variables (prefix ``APP_``) and, when present,
from a local ``.env`` file. No secrets have defaults: connection URLs are optional
until a component actually needs them.
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

    database_url: PostgresDsn | None = None
    redis_url: RedisDsn | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
