"""Deployment application factory: the operator entry point.

    uv run uvicorn app.bootstrap:create_deployment_app --factory \\
        --app-dir apps/api --env-file .env

settings -> Product auth (inside ``create_app``) -> deployment composition
(``APP_BUSINESS_BACKEND``) -> ``app.main.create_app`` with the composed services.

* ``disabled``: local/test only; Product business routes answer 503.
* ``mock``: local/test only; the real Product core on the deterministic mock backend.
* staging/production: refused (``DeploymentCompositionError``). No real business
  backend exists yet, and a deployment must not run on mock data or half-composed.

Integration management (Task 031) is composed independently of the business backend
(``app.composition.integrations``): connection metadata in PostgreSQL, credentials in
``APP_INTEGRATION_SECRETS_DIR``; this build installs no integration type. Agent
management (Task 032, ``app.composition.agents``) is composed the same way: Agent
enable/disable overrides in PostgreSQL, gating the Operations run boundary. Workflow
inspection (Task 034, ``app.composition.workflows``) is composed the same way: read-only
Workflow catalog and run history; Workflows themselves run inside the business backend.

The composition-owned resources are released when the application shuts down, or
immediately if the application cannot be built. Startup never migrates the database.
"""

from collections.abc import Callable

from agno.models.base import Model
from agno.os.settings import AgnoAPISettings
from fastapi import FastAPI

from app.composition import build_deployment_composition
from app.composition.agents import build_agent_management
from app.composition.integrations import build_integration_management
from app.composition.workflows import build_workflow_inspection
from app.config import Settings, get_settings
from app.main import create_app


def create_deployment_app(
    settings: Settings | None = None,
    runtime_settings: AgnoAPISettings | None = None,
    *,
    model: Model | None = None,
) -> FastAPI:
    """``model`` is an explicit model override for deterministic tests; it is the only
    override (Product authentication and the services always come from settings)."""
    settings = settings or get_settings()
    composition = build_deployment_composition(settings, model=model)
    discards: list[Callable[[], None]] = [composition.discard]

    def discard_all() -> None:
        # Release everything built so far, in reverse order; every release is attempted.
        error: BaseException | None = None
        for discard in reversed(discards):
            try:
                discard()
            except BaseException as failure:  # noqa: BLE001 - re-raised below
                error = error or failure
        if error is not None:
            raise error

    try:
        integrations = build_integration_management(settings)
        discards.append(integrations.discard)
        agents = build_agent_management(settings)
        discards.append(agents.discard)
        workflows = build_workflow_inspection(settings)
        discards.append(workflows.discard)
    except BaseException:
        discard_all()
        raise

    async def close() -> None:
        try:
            await composition.close()
        finally:
            try:
                await integrations.close()
            finally:
                try:
                    await agents.close()
                finally:
                    await workflows.close()

    try:
        return create_app(
            settings,
            runtime_settings,
            default_model=composition.default_model,
            operations_service=composition.operations_service,
            operations_ticket_service=composition.operations_ticket_service,
            operations_ticket_query_service=composition.operations_ticket_query_service,
            daily_operations_service=composition.daily_operations_service,
            integration_service=integrations.service,
            agent_service=agents.service,
            workflow_service=workflows.service,
            shutdown_callback=close,
        )
    except BaseException:
        discard_all()
        raise
