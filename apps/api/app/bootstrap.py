"""Deployment application factory: the operator entry point.

    uv run uvicorn app.bootstrap:create_deployment_app --factory \\
        --app-dir apps/api --env-file .env

settings -> Product auth (inside ``create_app``) -> deployment composition
(``APP_BUSINESS_BACKEND``) -> ``app.main.create_app`` with the composed services.

* ``disabled``: local/test only; Product business routes answer 503.
* ``mock``: local/test only; the real Product core on the deterministic mock backend.
* staging/production: refused (``DeploymentCompositionError``). No real business
  backend exists yet, and a deployment must not run on mock data or half-composed.

The composition-owned resources are released when the application shuts down, or
immediately if the application cannot be built. Startup never migrates the database.
"""

from agno.models.base import Model
from agno.os.settings import AgnoAPISettings
from fastapi import FastAPI

from app.composition import build_deployment_composition
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
    try:
        return create_app(
            settings,
            runtime_settings,
            default_model=composition.default_model,
            operations_service=composition.operations_service,
            operations_ticket_service=composition.operations_ticket_service,
            operations_ticket_query_service=composition.operations_ticket_query_service,
            shutdown_callback=composition.close,
        )
    except BaseException:
        composition.discard()
        raise
