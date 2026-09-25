"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.config import Settings, get_settings
from app.runtime import build_agent_runtime


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.agent_runtime = build_agent_runtime(settings.name)
        yield

    app = FastAPI(title=settings.name, version=__version__, debug=settings.debug, lifespan=lifespan)
    app.state.settings = settings

    @app.get("/health", tags=["system"])
    async def health() -> dict[str, object]:
        return {
            "status": "ok",
            "name": settings.name,
            "version": __version__,
            "environment": settings.environment,
            "agent_runtime": app.state.agent_runtime.status(),
        }

    return app


app = create_app()
