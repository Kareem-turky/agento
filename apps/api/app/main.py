"""Product API application factory.

The Product API is a FastAPI app; Agno AgentOS is attached to it as the agent
runtime (``AgentOS(base_app=...)``), producing one combined application.

Run with: ``uvicorn app.main:create_app --factory --app-dir apps/api``
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from agno.models.base import Model
from agno.os.settings import AgnoAPISettings
from fastapi import FastAPI

from app import __version__
from app.config import Settings, get_settings
from app.context import ActorResolver, NoActorResolver, RequestContextMiddleware
from app.routes.operations import OPERATIONS_RUNS_PATH, OPERATIONS_SERVICE_STATE_KEY
from app.routes.operations import router as operations_router
from app.runtime import attach_agent_os, resolve_runtime_settings, runtime_status
from app.services.operations import OperationsRunService


def create_app(
    settings: Settings | None = None,
    runtime_settings: AgnoAPISettings | None = None,
    default_model: Model | None = None,
    actor_resolver: ActorResolver | None = None,
    operations_service: OperationsRunService | None = None,
) -> FastAPI:
    """``operations_service`` is composed by the caller (no default: without one the
    Operations route answers 503; nothing falls back to mock data)."""
    settings = settings or get_settings()
    runtime_settings = resolve_runtime_settings(settings, runtime_settings)
    # AgentOS applies Agno's ``docs_enabled`` only to apps it creates itself; with a
    # base_app the docs routes come from this constructor, so use the same setting here.
    docs_enabled = runtime_settings.docs_enabled

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.runtime_started = True
        yield
        app.state.runtime_started = False

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

    app.state.agent_os = attach_agent_os(
        app,
        settings,
        runtime_settings,
        default_model,
        # Product-authenticated (ActorResolver), not AgentOS-key-authenticated.
        product_route_paths=(OPERATIONS_RUNS_PATH,),
    )
    # Added after AgentOS so it is the outermost middleware: every response, including
    # AgentOS auth rejections, carries the server-generated X-Request-ID.
    app.add_middleware(RequestContextMiddleware, resolver=actor_resolver or NoActorResolver())
    return app
