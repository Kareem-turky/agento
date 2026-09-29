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
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PostgresDsn,
    RedisDsn,
    StringConstraints,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["local", "test", "staging", "production"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
ModelProvider = Literal["disabled", "openai", "anthropic"]
ProductAuthMode = Literal["disabled", "api_key"]

_ConfigId = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=256)
]
# SHA-256 of the raw Product API key: exactly 64 lowercase hex characters.
KeySha256 = Annotated[str, StringConstraints(strict=True, pattern=r"^[0-9a-f]{64}$")]


class ProductApiKeyPrincipalConfig(BaseModel):
    """One configured Product API principal.

    Holds only the SHA-256 of the raw key (never the key itself). There is no
    per-key company: every principal belongs to the deployment's single
    ``Settings.company_id``. ``key_id`` is operator metadata and is never exposed.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    key_id: _ConfigId
    key_sha256: KeySha256
    actor_id: _ConfigId
    role_ids: frozenset[_ConfigId] = frozenset()
    permissions: frozenset[_ConfigId] = frozenset()
    store_ids: frozenset[_ConfigId] = frozenset()


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

    # Product API authentication (not the AgentOS OS_SECURITY_KEY). This deployment
    # serves exactly one company; every configured API principal belongs to it.
    product_auth_mode: ProductAuthMode = "disabled"
    company_id: _ConfigId | None = None
    product_api_keys: tuple[ProductApiKeyPrincipalConfig, ...] = ()

    @field_validator("company_id", mode="before")
    @classmethod
    def _blank_company_is_unset(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def _product_auth_is_consistent(self) -> Self:
        key_ids = [p.key_id for p in self.product_api_keys]
        hashes = [p.key_sha256 for p in self.product_api_keys]
        if len(set(key_ids)) != len(key_ids):
            raise ValueError("duplicate Product API key_id")
        if len(set(hashes)) != len(hashes):
            raise ValueError("duplicate Product API key hash")
        if self.product_auth_mode == "api_key":
            if self.company_id is None:
                raise ValueError("APP_COMPANY_ID is required when Product auth is api_key")
            if not self.product_api_keys:
                raise ValueError("APP_PRODUCT_API_KEYS needs at least one principal")
        elif self.environment not in DEVELOPMENT_ENVIRONMENTS:
            raise ValueError("Product authentication cannot be disabled in staging/production")
        return self

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
