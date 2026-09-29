"""Deployment composition: settings -> the Product services handed to ``create_app``.

    create_deployment_app (app.bootstrap)
      -> build_deployment_composition(settings, model=...)
           environment policy FIRST (nothing is constructed before it passes)
           disabled -> no Product business services (routes answer 503)
           mock     -> app.composition.local_mock (local/test only)
      -> app.main.create_app(..., services, shutdown_callback=composition.close)

There is no real business backend yet (no authoritative provider contract exists), so
staging and production have no allowed backend and deployment composition fails
closed there: never a mock, dummy or in-memory fallback. Nothing here migrates or
creates tables. Routes, services and the domain never import this package.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from agno.models.base import Model

from app.config import DEVELOPMENT_ENVIRONMENTS, Settings
from app.services.operations import OperationsRunService
from app.services.operations_reports import DailyOperationsReportService
from app.services.operations_tickets import (
    OperationsTicketCommandQueryService,
    OperationsTicketCommandService,
)

NO_DEPLOYMENT_BACKEND = "no business backend is available for staging/production deployments"
MOCK_NOT_ALLOWED = "the mock business backend is for local/test environments only"
MODEL_REQUIRED = "Operations model is required for mock business composition"
DATABASE_REQUIRED = "APP_DATABASE_URL is required for mock business composition"
UNKNOWN_BACKEND = "unsupported business backend"
RELEASE_FAILED = "Product resources could not be released"


class DeploymentCompositionError(RuntimeError):
    """The deployment cannot be composed. Fixed messages: no settings or credentials."""


async def _nothing_to_close() -> None:
    return None


def _nothing_to_discard() -> None:
    return None


@dataclass(frozen=True)
class DeploymentComposition:
    """What ``create_app`` needs, and how to release what was built for it.

    Only Product service contracts cross this boundary (no engine, session, store,
    coordinator or provider object). ``close`` is awaited once on application
    shutdown; ``discard`` releases resources synchronously when the application could
    not be built at all.
    """

    default_model: Model | None = None
    operations_service: OperationsRunService | None = None
    operations_ticket_service: OperationsTicketCommandService | None = None
    operations_ticket_query_service: OperationsTicketCommandQueryService | None = None
    daily_operations_service: DailyOperationsReportService | None = None
    close: Callable[[], Awaitable[None]] = _nothing_to_close
    discard: Callable[[], None] = _nothing_to_discard


def build_deployment_composition(
    settings: Settings, *, model: Model | None = None
) -> DeploymentComposition:
    """Compose the Product services for ``settings.business_backend``.

    ``model`` is an explicit Operations/default model override (deterministic test
    models); without it the configured default model is used. It changes no
    authentication or authorization.
    """
    backend = settings.business_backend
    if settings.environment not in DEVELOPMENT_ENVIRONMENTS:
        # Checked before anything is constructed: no mock provider, engine or model.
        raise DeploymentCompositionError(
            MOCK_NOT_ALLOWED if backend == "mock" else NO_DEPLOYMENT_BACKEND
        )
    if backend == "disabled":
        # Incomplete business surface on purpose: Product business routes answer 503.
        return DeploymentComposition(default_model=model)
    if backend == "mock":
        # Imported only here: mock providers never load for any other path.
        from app.composition.local_mock import build_local_mock_composition

        return build_local_mock_composition(settings, model=model)
    raise DeploymentCompositionError(UNKNOWN_BACKEND)
