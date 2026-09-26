"""Agno AgentOS runtime attached to the Product API.

Agno is an external, pinned dependency. This module only configures it:

* ``AgentOS(base_app=...)`` mounts the AgentOS routes onto our FastAPI app.
* ``PostgresDb`` persists Agno runtime data in its own schema (``agno_runtime``);
  Agno creates and owns the tables in that schema.
* ``OS_SECURITY_KEY`` (read by Agno's ``AgnoAPISettings``) protects the AgentOS
  routes. It is a temporary runtime guard, not product authentication.

The single registered agent is a non-production smoke test: no tools, no
memory, and a placeholder model that refuses execution (no provider, no keys).
"""

from importlib.metadata import version

from agno.agent import Agent
from agno.db.postgres import PostgresDb
from agno.os import AgentOS
from agno.os.settings import AgnoAPISettings
from fastapi import FastAPI

from app.config import Settings
from app.runtime.non_executing_model import NonExecutingModel

SMOKE_TEST_AGENT_ID = "runtime-smoke-test"
MIN_OS_SECURITY_KEY_LENGTH = 32
# Environments where the API docs (/docs, /redoc, /openapi.json) may be served.
DOCS_ENVIRONMENTS = frozenset({"local", "test"})


class RuntimeConfigurationError(RuntimeError):
    """Raised when required agent runtime configuration is missing."""


def _smoke_test_agent() -> Agent:
    return Agent(
        id=SMOKE_TEST_AGENT_ID,
        name="Runtime smoke test",
        model=NonExecutingModel(),
        description="Non-production agent used only to verify AgentOS registration. Never run.",
    )


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
    if settings.environment not in DOCS_ENVIRONMENTS and runtime_settings.docs_enabled:
        runtime_settings = runtime_settings.model_copy(update={"docs_enabled": False})
    return runtime_settings


def attach_agent_os(
    base_app: FastAPI,
    settings: Settings,
    runtime_settings: AgnoAPISettings | None = None,
) -> AgentOS:
    """Attach AgentOS to ``base_app`` in place and return the AgentOS instance."""
    if settings.database_url is None:
        raise RuntimeConfigurationError(
            "APP_DATABASE_URL is required: the agent runtime persists to PostgreSQL."
        )
    runtime_settings = resolve_runtime_settings(settings, runtime_settings)

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
        agents=[_smoke_test_agent()],
        settings=runtime_settings,
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
