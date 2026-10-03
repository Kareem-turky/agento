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
Product Knowledge (Task 035, ``app.composition.knowledge``) is composed the same way:
the versioned operating model and Knowledge documents in PostgreSQL. Human approval
(Task 036, ``app.composition.approvals``) is composed the same way; requests themselves
are created by the business composition's ExecutionCoordinator.

ONE Product observability per application: chosen here, then handed to the business
composition (the Workflow engine), the Knowledge composition and ``create_app`` alike.

The composition-owned resources are released when the application shuts down, or
immediately if the application cannot be built. Startup never migrates the database.
"""

from collections.abc import Callable

from agno.models.base import Model
from agno.os.settings import AgnoAPISettings
from fastapi import FastAPI

from app.composition import build_deployment_composition
from app.composition.agents import build_agent_management
from app.composition.approvals import build_approvals
from app.composition.integrations import build_integration_management
from app.composition.knowledge import build_knowledge
from app.composition.workflows import build_workflow_inspection
from app.config import Settings, get_settings
from app.main import create_app
from app.observability import ProductObservability, build_default_observability


def create_deployment_app(
    settings: Settings | None = None,
    runtime_settings: AgnoAPISettings | None = None,
    *,
    model: Model | None = None,
    observability: ProductObservability | None = None,
) -> FastAPI:
    """``model`` is an explicit model override for deterministic tests. ``observability``
    is the Product observability of the application (default: the Product's own); it is
    chosen ONCE here and the SAME instance is given to the business composition (Workflow
    runs and Step attempts) and to ``create_app`` (HTTP and service observations).
    Product authentication and the services always come from settings."""
    settings = settings or get_settings()
    observer = observability if observability is not None else build_default_observability()
    composition = build_deployment_composition(settings, model=model, observability=observer)
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
        knowledge = build_knowledge(settings, observability=observer)
        discards.append(knowledge.discard)
        approvals = build_approvals(settings, observability=observer,
                                    workflows=composition.approval_workflow_resumer)  # fmt: skip
        discards.append(approvals.discard)
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
                    try:
                        await workflows.close()
                    finally:
                        try:
                            await knowledge.close()
                        finally:
                            await approvals.close()

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
            knowledge_service=knowledge.service,
            approval_service=approvals.service,
            shutdown_callback=close,
            observability=observer,
        )
    except BaseException:
        discard_all()
        raise
