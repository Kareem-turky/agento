"""Composition contracts shared by deployment selection, the backend registry and the
backend-specific builders (kept here to avoid import cycles).

Only Product service contracts and lifecycle callables cross this boundary: never an
engine, session, store, sink, integration adapter or registry internal.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from agno.models.base import Model

from app.composition.backend_inputs import BusinessBackendInputs
from app.config import Settings
from app.observability.contracts import ProductObservability
from app.services.operations import OperationsRunService
from app.services.operations_reports import DailyOperationsReportService
from app.services.operations_tickets import (
    OperationsTicketCommandQueryService,
    OperationsTicketCommandService,
)

# Fixed, provider-neutral messages: never a backend id, setting, URL or credential.
NO_DEPLOYMENT_BACKEND = "no business backend is available for staging/production deployments"
UNSUPPORTED_BACKEND = "unsupported business backend"
BACKEND_NOT_ALLOWED = "selected business backend is not allowed in this environment"
INVALID_COMPOSITION = "business backend produced an invalid composition"
MODEL_REQUIRED = "Operations model is required for mock business composition"
DATABASE_REQUIRED = "APP_DATABASE_URL is required for mock business composition"
RELEASE_FAILED = "Product resources could not be released"
BACKEND_INPUTS_UNAVAILABLE = "business backend inputs are unavailable"


class DeploymentCompositionError(RuntimeError):
    """The deployment cannot be composed. Fixed messages: no settings or credentials."""


async def _nothing_to_close() -> None:
    return None


def _nothing_to_discard() -> None:
    return None


@dataclass(frozen=True)
class DeploymentComposition:
    """What ``create_app`` needs, and how to release what was built for it.

    ``close`` is awaited once on application shutdown; ``discard`` releases resources
    synchronously when the application could not be built at all.
    """

    default_model: Model | None = None
    operations_service: OperationsRunService | None = None
    operations_ticket_service: OperationsTicketCommandService | None = None
    operations_ticket_query_service: OperationsTicketCommandQueryService | None = None
    daily_operations_service: DailyOperationsReportService | None = None
    close: Callable[[], Awaitable[None]] = _nothing_to_close
    discard: Callable[[], None] = _nothing_to_discard


class BusinessBackendBuilder(Protocol):
    """Builds the Product services for one registered business backend.

    ``inputs`` holds exactly the config and secret inputs the registration declared,
    already resolved by the Product. A builder never reads the environment, files or
    deployment secret storage itself, and reveals a secret only where it is used.

    ``observability`` is the ONE Product observability of the deployed application (the
    same instance ``create_app`` uses); a builder hands it to the services it composes
    that observe themselves (the Workflow engine). ``None``: those services are not
    observed. A builder never creates another one.
    """

    def __call__(
        self,
        settings: Settings,
        *,
        model: Model | None = None,
        inputs: BusinessBackendInputs,
        observability: ProductObservability | None = None,
    ) -> DeploymentComposition: ...
