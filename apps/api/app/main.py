"""Product API application factory.

The Product API is a FastAPI app; Agno AgentOS is attached to it as the agent
runtime (``AgentOS(base_app=...)``), producing one combined application.

Run with: ``uvicorn app.main:create_app --factory --app-dir apps/api``
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from agno.os.settings import AgnoAPISettings
from fastapi import FastAPI

from app import __version__
from app.config import Settings, get_settings
from app.runtime import attach_agent_os, runtime_status


def create_app(
    settings: Settings | None = None,
    runtime_settings: AgnoAPISettings | None = None,
) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.runtime_started = True
        yield
        app.state.runtime_started = False

    app = FastAPI(title=settings.name, version=__version__, debug=settings.debug, lifespan=lifespan)
    app.state.settings = settings
    app.state.runtime_started = False

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

    app.state.agent_os = attach_agent_os(app, settings, runtime_settings)
    return app
