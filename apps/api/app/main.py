"""Product API application factory.

The Product API is a FastAPI app; Agno AgentOS is attached to it as the agent
runtime (``AgentOS(base_app=...)``), producing one combined application.

This is the LOW-LEVEL, injection-oriented factory (tests and explicit compositions).
It builds no Product persistence, integrations, coordinators or agents and applies no
business-backend policy. Operators run the deployment factory instead, which composes
the Product services and then calls this function:
``uvicorn app.bootstrap:create_deployment_app --factory --app-dir apps/api``.
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from agno.models.base import Model
from agno.os.settings import AgnoAPISettings
from fastapi import FastAPI

from app import __version__
from app.auth import build_actor_resolver, validate_credential_separation
from app.config import Settings, get_settings
from app.context import ActorResolver, RequestContextMiddleware
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
from app.runtime import attach_agent_os, resolve_runtime_settings, runtime_status
from app.services.operations import OperationsRunService
from app.services.operations_reports import DailyOperationsReportService
from app.services.operations_tickets import (
    OperationsTicketCommandQueryService,
    OperationsTicketCommandService,
)


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
    one."""
    settings = settings or get_settings()
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
    setattr(app.state, OPERATIONS_SERVICE_STATE_KEY, operations_service)
    setattr(app.state, OPERATIONS_TICKET_SERVICE_STATE_KEY, operations_ticket_service)
    setattr(app.state, OPERATIONS_TICKET_QUERY_SERVICE_STATE_KEY, operations_ticket_query_service)
    setattr(app.state, OPERATIONS_DAILY_REPORT_SERVICE_STATE_KEY, daily_operations_service)

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
        ),
    )
    # Added after AgentOS so it is the outermost middleware: every response, including
    # AgentOS auth rejections, carries the server-generated X-Request-ID.
    app.add_middleware(RequestContextMiddleware, resolver=resolver)
    return app
