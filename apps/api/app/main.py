"""Product API application factory.

The Product API is a FastAPI app; Agno AgentOS is attached to it as the agent
runtime (``AgentOS(base_app=...)``), producing one combined application.

This is the LOW-LEVEL, injection-oriented factory (tests and explicit compositions).
It builds no Product persistence, integrations, coordinators or agents and applies no
business-backend policy. Operators run the deployment factory instead, which composes
the Product services and then calls this function:
``uvicorn app.bootstrap:create_deployment_app --factory --app-dir apps/api``.
"""

import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from agno.models.base import Model
from agno.os.settings import AgnoAPISettings
from fastapi import FastAPI

from app import __version__
from app.agent_management.runtime import AgentGatedOperationsRunService
from app.agent_management.service import AgentManagementService
from app.approval_management.service import ApprovalService
from app.auth import build_actor_resolver, validate_credential_separation
from app.config import Settings, get_settings
from app.context import ActorResolver, RequestContextMiddleware
from app.conversations.service import ConversationReadService
from app.integration_management.service import IntegrationManagementService
from app.knowledge.service import KnowledgeService
from app.observability import (
    ProductObservability,
    ProductObservabilityMiddleware,
    build_default_observability,
)
from app.observability.services import (
    observed_daily_operations_service,
    observed_operations_service,
    observed_ticket_query_service,
    observed_ticket_service,
)
from app.routes.agents import AGENTS_PATHS, AGENTS_SERVICE_STATE_KEY
from app.routes.agents import router as agents_router
from app.routes.approvals import APPROVALS_PATHS, APPROVALS_SERVICE_STATE_KEY
from app.routes.approvals import router as approvals_router
from app.routes.capabilities import CAPABILITIES_PATHS
from app.routes.capabilities import router as capabilities_router
from app.routes.conversations import CONVERSATIONS_PATHS, CONVERSATIONS_SERVICE_STATE_KEY
from app.routes.conversations import router as conversations_router
from app.routes.integrations import INTEGRATIONS_PATHS, INTEGRATIONS_SERVICE_STATE_KEY
from app.routes.integrations import router as integrations_router
from app.routes.knowledge import KNOWLEDGE_PATHS, KNOWLEDGE_SERVICE_STATE_KEY
from app.routes.knowledge import router as knowledge_router
from app.routes.operations import OPERATIONS_RUNS_PATH, OPERATIONS_SERVICE_STATE_KEY
from app.routes.operations import router as operations_router
from app.routes.operations_reports import (
    OPERATIONS_DAILY_REPORT_PATH,
    OPERATIONS_DAILY_REPORT_SERVICE_STATE_KEY,
)
from app.routes.operations_reports import router as operations_reports_router
from app.routes.operations_tickets import (
    OPERATIONS_TICKET_COMMANDS_PATH,
    OPERATIONS_TICKET_QUERY_SERVICE_STATE_KEY,
    OPERATIONS_TICKET_SERVICE_STATE_KEY,
    OPERATIONS_TICKETS_PATH,
)
from app.routes.operations_tickets import router as operations_tickets_router
from app.routes.system import PUBLIC_HEALTH_PATHS, SYSTEM_PATHS, SYSTEM_SERVICE_STATE_KEY
from app.routes.system import router as system_router
from app.routes.workflows import WORKFLOWS_PATHS, WORKFLOWS_SERVICE_STATE_KEY
from app.routes.workflows import router as workflows_router
from app.runtime import attach_agent_os, resolve_runtime_settings, runtime_status
from app.services.operations import OperationsRunService
from app.services.operations_reports import DailyOperationsReportService
from app.services.operations_tickets import (
    OperationsTicketCommandQueryService,
    OperationsTicketCommandService,
)
from app.system_operations import (
    DatabaseReadinessProbe,
    LifecycleSnapshot,
    SystemOperationsService,
    TelemetryExportMode,
)
from app.workflow_management.service import WorkflowInspectionService


def create_app(
    settings: Settings | None = None,
    runtime_settings: AgnoAPISettings | None = None,
    default_model: Model | None = None,
    actor_resolver: ActorResolver | None = None,
    operations_service: OperationsRunService | None = None,
    operations_ticket_service: OperationsTicketCommandService | None = None,
    operations_ticket_query_service: OperationsTicketCommandQueryService | None = None,
    daily_operations_service: DailyOperationsReportService | None = None,
    shutdown_callback: Callable[[], Awaitable[None]] | None = None,
    observability: ProductObservability | None = None,
    integration_service: IntegrationManagementService | None = None,
    agent_service: AgentManagementService | None = None,
    workflow_service: WorkflowInspectionService | None = None,
    knowledge_service: KnowledgeService | None = None,
    approval_service: ApprovalService | None = None,
    conversation_service: ConversationReadService | None = None,
    system_probe: DatabaseReadinessProbe | None = None,
) -> FastAPI:
    """``operations_service``, ``operations_ticket_service``,
    ``operations_ticket_query_service`` and ``daily_operations_service`` are composed by
    the caller (no defaults: without one the matching route answers 503; nothing falls
    back to mock data, and no store, reader, coordinator, workflow or integration is
    built here).

    ``actor_resolver`` overrides Product authentication; by default it is built from
    settings (``APP_PRODUCT_AUTH_MODE``), see ``app.auth.build_actor_resolver``.

    ``shutdown_callback`` is a generic hook awaited once when the application lifespan
    ends (e.g. the caller releasing resources it composed); nothing is called without
    one.

    ``observability`` is the Product observability (structured completion logs,
    OpenTelemetry API traces and metrics; nothing is exported over the network). By
    default the Product's own implementation is used; tests inject recording or failing
    ones. Composed services are wrapped in their observed decorators generically; a
    missing service stays missing.

    ``integration_service`` is the provider-independent integration management (Task
    031), composed independently of the business backend; without one the integration
    routes answer 503.

    ``agent_service`` is the Product Agent management (Task 032). With one, the Agent
    routes are served and the Operations run boundary is gated by the effective state of
    the ``operations`` Agent (a disabled Agent is refused before it, its model or any tool
    runs). Without one, the Agent routes answer 503 and every Agent keeps its Product
    definition default.

    ``workflow_service`` is the read-only Workflow inspection (Task 034): the Workflow
    catalog and this company's run history. Without one the Workflow routes answer 503.
    It never executes anything (there is no Workflow run endpoint).

    ``knowledge_service`` is Product Knowledge & company operating context (Task 035):
    the versioned CompanyOperatingModel and Knowledge documents with bounded,
    company-scoped retrieval. Without one the Knowledge routes answer 503. No Agent
    consumes Knowledge in this build.
    ``approval_service`` is Product human approval (Task 036): reading requests and the
    human decisions on them (there is no create route: requests come only from
    governance). Without one the Approval routes answer 503.
    ``conversation_service`` is the read-only Product Conversation inspection (Task 037):
    canonical conversations and their transcripts (no ingest, webhook or send route).
    Without one the Conversation routes answer 503.
    ``system_probe`` is the bounded PostgreSQL / Product schema readiness probe (Task 039),
    composed by the caller. ``/health/live`` never uses it; ``/health/ready`` and the
    Product-authenticated ``/api/v1/system/status`` evaluate it FRESH on every call
    together with the lifespan and Agent runtime state. Without one the instance is never
    ready (``/health/ready`` answers 503), because PostgreSQL readiness cannot be shown."""
    settings = settings or get_settings()
    observer = observability if observability is not None else build_default_observability()
    runtime_settings = resolve_runtime_settings(settings, runtime_settings)
    # An explicitly injected resolver is used exactly; otherwise Product authentication
    # comes from settings (API keys), and staging/production refuse to start without it.
    if actor_resolver is not None:
        resolver = actor_resolver
    else:
        resolver = build_actor_resolver(settings)
        if settings.product_auth_mode == "api_key":
            # One credential must never open both surfaces: refuse a Product key that is
            # the AgentOS key. Only the OS key string crosses into the auth module.
            validate_credential_separation(settings, runtime_settings.os_security_key)
    # AgentOS applies Agno's ``docs_enabled`` only to apps it creates itself; with a
    # base_app the docs routes come from this constructor, so use the same setting here.
    docs_enabled = runtime_settings.docs_enabled

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.started_at = time.monotonic()
        app.state.runtime_started = True
        try:
            yield
        finally:
            app.state.runtime_started = False
            if shutdown_callback is not None:
                await shutdown_callback()

    app = FastAPI(
        title=settings.name,
        version=__version__,
        debug=settings.debug,
        lifespan=lifespan,
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
    )
    app.state.settings = settings
    app.state.runtime_started = False
    app.state.started_at = None
    if agent_service is not None:
        # Which Product Agents' trusted runtimes this deployment actually composed.
        agent_service = agent_service.with_runtime(
            frozenset({"operations"}) if operations_service is not None else frozenset()
        )
        if operations_service is not None:
            operations_service = AgentGatedOperationsRunService(operations_service, agent_service)
    setattr(app.state, AGENTS_SERVICE_STATE_KEY, agent_service)
    setattr(
        app.state,
        OPERATIONS_SERVICE_STATE_KEY,
        observed_operations_service(operations_service, observer),
    )
    setattr(
        app.state,
        OPERATIONS_TICKET_SERVICE_STATE_KEY,
        observed_ticket_service(operations_ticket_service, observer),
    )
    setattr(
        app.state,
        OPERATIONS_TICKET_QUERY_SERVICE_STATE_KEY,
        observed_ticket_query_service(operations_ticket_query_service, observer),
    )
    setattr(app.state, OPERATIONS_DAILY_REPORT_SERVICE_STATE_KEY,
            observed_daily_operations_service(daily_operations_service, observer))  # fmt: skip
    setattr(app.state, INTEGRATIONS_SERVICE_STATE_KEY, integration_service)
    setattr(app.state, WORKFLOWS_SERVICE_STATE_KEY, workflow_service)
    setattr(app.state, KNOWLEDGE_SERVICE_STATE_KEY, knowledge_service)
    setattr(app.state, APPROVALS_SERVICE_STATE_KEY, approval_service)
    setattr(app.state, CONVERSATIONS_SERVICE_STATE_KEY, conversation_service)

    def lifecycle() -> LifecycleSnapshot:
        return LifecycleSnapshot(
            application_started=bool(app.state.runtime_started),
            agent_runtime_attached=getattr(app.state, "agent_os", None) is not None,
            started_at=app.state.started_at,
        )

    setattr(
        app.state,
        SYSTEM_SERVICE_STATE_KEY,
        SystemOperationsService(
            probe=system_probe,
            lifecycle=lifecycle,
            version=__version__,
            environment=settings.environment,
            export_mode=TelemetryExportMode(settings.otel_export_mode),
            observability=observer,
        ),
    )

    @app.get("/health", tags=["system"])
    async def health() -> dict[str, object]:
        return {
            "status": "ok",
            "application": {
                "name": settings.name,
                "version": __version__,
                "environment": settings.environment,
            },
            "agent_runtime": runtime_status(app.state.agent_os, app.state.runtime_started),
        }

    # Product routes are registered before AgentOS attaches its own routers.
    app.include_router(operations_router)
    app.include_router(operations_reports_router)
    app.include_router(operations_tickets_router)
    app.include_router(integrations_router)
    app.include_router(agents_router)
    app.include_router(capabilities_router)
    app.include_router(workflows_router)
    app.include_router(knowledge_router)
    app.include_router(approvals_router)
    app.include_router(conversations_router)
    app.include_router(system_router)

    # Excluded from the AgentOS key: the Product-authenticated paths, plus the public
    # container health probes (no credential at all, like AgentOS's own /health).
    app.state.agent_os = attach_agent_os(
        app,
        settings,
        runtime_settings,
        default_model,
        # Product-authenticated (ActorResolver), not AgentOS-key-authenticated.
        product_route_paths=(
            OPERATIONS_RUNS_PATH,
            OPERATIONS_DAILY_REPORT_PATH,
            OPERATIONS_TICKETS_PATH,
            OPERATIONS_TICKET_COMMANDS_PATH,
            *INTEGRATIONS_PATHS,
            *AGENTS_PATHS,
            *CAPABILITIES_PATHS,
            *WORKFLOWS_PATHS,
            *KNOWLEDGE_PATHS,
            *APPROVALS_PATHS,
            *CONVERSATIONS_PATHS,
            *SYSTEM_PATHS,
            *PUBLIC_HEALTH_PATHS,
        ),
    )
    # Product HTTP observability sits just inside the request context: it observes only
    # the exact Product paths (never AgentOS) and sees the server-generated request id.
    app.add_middleware(ProductObservabilityMiddleware, observability=observer)
    # Added last so it is the outermost middleware: every response, including AgentOS
    # auth rejections, carries the server-generated X-Request-ID.
    app.add_middleware(RequestContextMiddleware, resolver=resolver)
    return app
