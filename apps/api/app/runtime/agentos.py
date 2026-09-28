"""Agno AgentOS runtime attached to the Product API.

Agno is an external, pinned dependency. This module only configures it:

* ``AgentOS(base_app=...)`` mounts the AgentOS routes onto our FastAPI app.
* ``PostgresDb`` persists Agno runtime data in its own schema (``agno_runtime``);
  Agno creates and owns the tables in that schema.
* ``OS_SECURITY_KEY`` (read by Agno's ``AgnoAPISettings``) protects the AgentOS
  routes. It is a temporary runtime guard, not product authentication.
* Product routes authenticate through the product ``ActorResolver`` instead, so
  their exact paths are exempted from the AgentOS auth layer (Agno's documented
  ``AuthorizationConfig.excluded_route_paths``). Every AgentOS route still requires
  the key.

Which agents are registered is decided in ``app.runtime.components``; the
default model comes from ``app.runtime.models``.
"""

from importlib.metadata import version

from agno.db.postgres import PostgresDb
from agno.models.base import Model
from agno.os import AgentOS
from agno.os.config import AuthorizationConfig
from agno.os.settings import AgnoAPISettings
from fastapi import FastAPI

from app.config import Settings
from app.runtime.components import build_agents
from app.runtime.errors import RuntimeConfigurationError
from app.runtime.models import build_default_model
from app.runtime.telemetry import enforce_telemetry_policy

MIN_OS_SECURITY_KEY_LENGTH = 32


def resolve_runtime_settings(
    settings: Settings, runtime_settings: AgnoAPISettings | None = None
) -> AgnoAPISettings:
    """Validate Agno's runtime settings and apply the product's environment policy.

    * ``OS_SECURITY_KEY`` must be set and at least 32 characters long.
    * Agno's ``docs_enabled`` is forced off outside local/test environments.
    """
    runtime_settings = runtime_settings or AgnoAPISettings()
    key = (runtime_settings.os_security_key or "").strip()
    if not key:
        raise RuntimeConfigurationError(
            "OS_SECURITY_KEY is required: AgentOS routes must not be served unauthenticated."
        )
    if len(key) < MIN_OS_SECURITY_KEY_LENGTH:
        raise RuntimeConfigurationError(
            f"OS_SECURITY_KEY must be at least {MIN_OS_SECURITY_KEY_LENGTH} characters long "
            "(generate one with: openssl rand -hex 32)."
        )
    if not settings.is_development and runtime_settings.docs_enabled:
        runtime_settings = runtime_settings.model_copy(update={"docs_enabled": False})
    return runtime_settings


def attach_agent_os(
    base_app: FastAPI,
    settings: Settings,
    runtime_settings: AgnoAPISettings | None = None,
    default_model: Model | None = None,
    product_route_paths: tuple[str, ...] = (),
) -> AgentOS:
    """Attach AgentOS to ``base_app`` in place and return the AgentOS instance.

    ``default_model`` overrides the model built from settings (used by tests to run
    agents without a provider). ``product_route_paths`` are exact product paths that
    use product authentication instead of the AgentOS key (no wildcards).
    """
    if any(any(c in path for c in "*?[") for path in product_route_paths):
        raise RuntimeConfigurationError("product route paths must be exact, not patterns")
    if settings.database_url is None:
        raise RuntimeConfigurationError(
            "APP_DATABASE_URL is required: the agent runtime persists to PostgreSQL."
        )
    runtime_settings = resolve_runtime_settings(settings, runtime_settings)
    enforce_telemetry_policy()
    if default_model is None:
        default_model = build_default_model(settings)

    db = PostgresDb(
        id="agno-runtime-db",
        db_url=str(settings.database_url),
        db_schema=settings.agno_db_schema,
    )
    agent_os = AgentOS(
        id=f"{settings.name}-runtime",
        name=f"{settings.name} runtime",
        base_app=base_app,
        # Product routes (e.g. /health) win over AgentOS built-ins with the same path.
        on_route_conflict="preserve_base_app",
        db=db,
        agents=build_agents(settings, default_model),
        settings=runtime_settings,
        authorization_config=AuthorizationConfig(excluded_route_paths=list(product_route_paths)),
        telemetry=False,
    )
    agent_os.get_app()
    return agent_os


def runtime_status(agent_os: AgentOS | None, started: bool) -> dict[str, object]:
    return {
        "framework": "agno",
        "version": version("agno"),
        "status": "ready" if agent_os is not None and started else "starting",
    }
