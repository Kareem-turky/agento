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
from pathlib import Path
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit

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
ModelProvider = Literal["disabled", "openai", "anthropic", "demo"]
# LOCAL-DEMO-ONLY deterministic model (``app.runtime.demo_model``): never a provider for a
# deployment, so it is refused outside the development environments (fail closed).
DEMO_MODEL_PROVIDER = "demo"
ProductAuthMode = Literal["disabled", "api_key"]
# Product OpenTelemetry export (Task 039). Disabled by default: no exporter, no thread,
# no network. ``otlp_http`` sends the existing bounded Product traces and metrics to ONE
# operator-chosen OTLP/HTTP collector base URL (``/v1/traces`` and ``/v1/metrics``).
OtelExportMode = Literal["disabled", "otlp_http"]
# Which business backend the deployment composition root (``app.bootstrap``) selects:
# a stable, Product-owned backend plugin IDENTIFIER, never a module, class, import path,
# URL or code. Settings validate only its syntax (no normalization); whether an id is
# installed is decided by the Product-owned allowlist in ``app.composition.registry``.
# "disabled" is a reserved Product sentinel (no business backend), not a plugin.
BUSINESS_BACKEND_ID_PATTERN = r"^[a-z0-9][a-z0-9._-]{0,63}$"
BusinessBackendId = Annotated[
    str, StringConstraints(strict=True, pattern=BUSINESS_BACKEND_ID_PATTERN)
]
DISABLED_BUSINESS_BACKEND = "disabled"

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
        # Validation errors never echo raw input (an endpoint or DSN may embed a credential).
        hide_input_in_errors=True,
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

    # Deployment composition only (``app.bootstrap``); availability and environment
    # policy are enforced there (the registry), not here, so the low-level factory stays
    # injectable in every environment.
    business_backend: BusinessBackendId = DISABLED_BUSINESS_BACKEND

    # Where the selected backend's declared inputs live (paths only, never values):
    # ``<backend_config_dir>/<NAME>`` and ``<backend_secrets_dir>/<NAME>``. Read once at
    # startup by ``app.composition`` and only for the names the selected backend's
    # registration declares; a backend that declares nothing needs neither directory.
    backend_config_dir: Path | None = None
    backend_secrets_dir: Path | None = None

    # Integration credentials (Task 031): the root directory of the filesystem
    # integration secret store. No default path: unset means no secret storage, and
    # connections that need credentials are refused (fail closed). Values never live in
    # PostgreSQL; protection relies on this directory's deployment/volume security.
    integration_secrets_dir: Path | None = None

    # Product OpenTelemetry export (Task 039): a startup-only setting, never exposed by
    # the Product API, UI or logs. The endpoint is a collector BASE URL: http(s), a host,
    # no credentials, query or fragment; the OTLP paths are appended by the Product.
    otel_export_mode: OtelExportMode = "disabled"
    otel_export_endpoint: str | None = None

    @field_validator("backend_config_dir", "backend_secrets_dir", "integration_secrets_dir",
                     mode="before")  # fmt: skip
    @classmethod
    def _blank_backend_dir_is_unset(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("otel_export_endpoint", mode="before")
    @classmethod
    def _blank_endpoint_is_unset(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def _otel_export_is_consistent(self) -> Self:
        # A supplied endpoint is always validated, even while export is disabled (then it
        # is unused): a malformed or credential-bearing value never sits silently here.
        if self.otel_export_endpoint is not None and not is_safe_otlp_endpoint(
            self.otel_export_endpoint
        ):
            raise ValueError(
                "APP_OTEL_EXPORT_ENDPOINT must be an http(s) collector base URL without "
                "credentials, query or fragment"
            )
        if self.otel_export_mode == "otlp_http" and self.otel_export_endpoint is None:
            raise ValueError("APP_OTEL_EXPORT_ENDPOINT is required for otlp_http export")
        return self

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

    @model_validator(mode="after")
    def _demo_model_is_development_only(self) -> Self:
        if (
            self.default_model_provider == DEMO_MODEL_PROVIDER
            and self.environment not in DEVELOPMENT_ENVIRONMENTS
        ):
            raise ValueError("the demo model provider is not allowed in staging/production")
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


def is_safe_otlp_endpoint(raw: str) -> bool:
    """An http(s) base URL with a host: no userinfo, query, fragment or whitespace."""
    try:
        parsed = urlsplit(raw)
        port = parsed.port  # raises on a malformed port
    except ValueError:
        return False
    del port
    return (
        raw == raw.strip()
        and not any(c.isspace() for c in raw)
        and parsed.scheme in ("http", "https")
        and bool(parsed.hostname)
        and "@" not in parsed.netloc
        and not parsed.query
        and not parsed.fragment
        and "?" not in raw
        and "#" not in raw
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
